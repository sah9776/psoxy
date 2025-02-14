#!/bin/python3
# -*- coding: utf-8 -*-

raise NotImplementedError("Not Updated To Support the New Protocol")

# Network
import socket
import select
from struct import pack, unpack
# System
import traceback
from threading import Thread, active_count
from signal import signal, SIGINT, SIGTERM
from time import sleep
import sys
import random
import string
from optparse import OptionParser
from aes import AESCipher

parser = OptionParser()
parser.add_option("-c", "--use-external-config",
                  action="store_true", dest="use_external_config", default=False,
                  help="uses the external configs in './config.py'")

(options, args) = parser.parse_args()

if options.use_external_config:
    from config import *
else:
    #
    # Configuration
    #
    MAX_THREADS = 200
    BUFSIZE = 16384
    SEND_CHUNK_SIZE = 1024
    TIMEOUT_SOCKET = 5
    LOCAL_ADDR = '0.0.0.0'
    LOCAL_PORT = 2153
    LOCAL_UUID='b050bc40-d8be-45df-aabc-60e0515d935a'
    OUTGOING_INTERFACE = ""
    # GTP HEADER TEMPLATE
    GTP_HEADER_FLAGS=48
    GTP_HEADER_TYPE=255
    GTP_HEADER_ID=b"\x00\x00\x79\x32"
    # RANDOM PACKET
    RANDOM_PACKET_LEN_RANGE=(256,1024)
    # SERVER OK
    SERVER_OK=b'OK'
    SERVER_PORT=2152
    SERVER_ADDR="127.0.0.1"
    SERVER_UUID='19237dd2-65a9-4783-9ca6-6202dfffe4b5'

def random_packet() -> str:
    N = random.randint(*RANDOM_PACKET_LEN_RANGE)
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=N))

class ExitStatus:
    """ Manage exit status """
    def __init__(self):
        self.exit = False

    def set_status(self, status):
        """ set exist status """
        self.exit = status

    def get_status(self):
        """ get exit status """
        return self.exit


def error(msg="", err=None):
    """ Print exception stack trace python """
    if msg:
        traceback.print_exc()
        try:
            print("{} - Code: {}, Message: {}".format(msg, str(err[0]), err[1]))
        except TypeError:
            print(f"{msg} - {err}")
    else:
        traceback.print_exc()

def proxy_loop(socket_src, socket_dst, teid: int, aes_client, aes_server):
    """ Wait for network activity """
    while not EXIT.get_status():
        try:
            reader, _, _ = select.select([socket_src, socket_dst], [], [], 1)
        except select.error as err:
            error("Select failed", err)
            return
        if not reader:
            continue
        try:
            for sock in reader:
                data = sock.recv(BUFSIZE)
                if not data:
                    return
                if sock is socket_dst:
                    payload = aes_server.decrypt(data)
                    for i in range(0, len(payload), SEND_CHUNK_SIZE):
                        payload_chunk = data[i:i+SEND_CHUNK_SIZE]
                        socket_src.send(aes_client.encrypt(payload_chunk))
                else:
                    payload = aes_client.decrypt(data)
                    for i in range(0, len(payload), SEND_CHUNK_SIZE):
                        payload_chunk = data[i:i+SEND_CHUNK_SIZE]
                        socket_dst.send(aes_server.encrypt(payload_chunk))
        except socket.error as err:
            error("Loop failed", err)
            return

def connect_to_dst(dst_addr, dst_port):
    """ Connect to desired destination """
    sock = create_socket()
    if OUTGOING_INTERFACE:
        try:
            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_BINDTODEVICE,
                OUTGOING_INTERFACE.encode(),
            )
        except PermissionError as err:
            print("Only root can set OUTGOING_INTERFACE parameter")
            EXIT.set_status(True)
    try:
        sock.connect((dst_addr, dst_port))
        return sock
    except socket.error as err:
        error("Failed to connect to DST", err)
        return 0

def request_client(wrapper, aes_client):
    """ Client request details """
    # +-----+----+-----+----------+----------+------+----------+----------+
    # | GTP | ID | LEN | DST.PORT | SRC.PORT | ATYP | DST.ADDR | SRC.ADDR |
    # +-----+----+-----+----------+----------+------+----------+----------+
    try:
        gtp_request = wrapper.recv(BUFSIZE)
    except ConnectionResetError:
        if wrapper != 0:
            wrapper.close()
        error()
        return False
    
    gtp_header_flags, gtp_header_type = unpack("!BB", gtp_request[:2])
    
    # Check VER, CMD and RSV
    if (
            gtp_header_flags != GTP_HEADER_FLAGS or
            gtp_header_type != GTP_HEADER_TYPE
    ):
        return False
    
    teid = unpack("!4s", gtp_request[4:8])[0]

    decrypted_gtp_request = aes_client.decrypt(gtp_request[8:])
    
    _, _, dst_port, _, atype = unpack("!HHHHB", decrypted_gtp_request[:9])
    atype = int(atype)

    # IPV4
    if atype == 0:
        dst_addr = socket.inet_ntoa(decrypted_gtp_request[9:13])
    # DOMAIN NAME
    elif atype == 1:
        dst_addr_size = unpack('!H', decrypted_gtp_request[9:11])[0]
        dst_addr = decrypted_gtp_request[11: 11 + dst_addr_size]
    else:
        return False

    print(dst_addr, dst_port)
    return (atype, dst_addr, dst_port, teid)

def request(wrapper):
    aes_client = AESCipher(SEND_CHUNK_SIZE, LOCAL_UUID)
    dst = request_client(wrapper, aes_client)
    if dst:
        # socket_dst = connect_to_dst(dst[0], dst[1])
        atype = dst[0]
        teid = dst[3]
        dst = dst[1:3]
        socket_dst = connect_to_dst(SERVER_ADDR, SERVER_PORT)
    if dst and socket_dst != 0:
        # Connect to server
        _random_packet = random_packet().encode()
        aes_server = AESCipher(SEND_CHUNK_SIZE, SERVER_UUID)
        if atype == 0: # IPv4
            header = pack("!HHHHB4s4s",
                0,
                len(_random_packet),
                dst[1],
                0,
                0,
                socket.inet_aton(dst[0]),
                socket.inet_aton("0.0.0.0")
            )
        else: # Host Name
            header = pack("!HHHHBH",
                0,
                len(_random_packet),
                dst[1],
                0,
                1,
                len(dst[0])
            ) + dst[0] + pack("!4s",
                socket.inet_aton("0.0.0.0")
            )
        packet = aes_server.encrypt(header + _random_packet)
        socket_dst.send(pack("!BBH4s",
                GTP_HEADER_FLAGS,
                GTP_HEADER_TYPE,
                len(packet),
                teid
        ) + packet)
        if aes_server.decrypt(socket_dst.recv(BUFSIZE)[8:]) == SERVER_OK:
            ack_payload = aes_client.encrypt(SERVER_OK)
            wrapper.send(pack("!BBH4s",
                    GTP_HEADER_FLAGS,
                    GTP_HEADER_TYPE,
                    len(ack_payload),
                    teid
            ) + ack_payload)

            # start proxy
            proxy_loop(wrapper, socket_dst, teid, aes_client, aes_server)
    if wrapper != 0:
        wrapper.close()
    if dst and socket_dst != 0:
        socket_dst.close()

def connection(wrapper):
    """ Function run by a thread """
    request(wrapper)

def create_socket():
    """ Create an INET, STREAMing socket """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT_SOCKET)
    except socket.error as err:
        error("Failed to create socket", err)
        sys.exit(0)
    return sock

def create_socket_udp() -> socket:
    """ Create an INET, STREAMing socket """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(TIMEOUT_SOCKET)
    except socket.error as err:
        error("Failed to create socket", err)
        sys.exit(0)
    return sock

def bind_port(sock: socket):
    """
        Bind the socket to address and
        listen for connections made to the socket
    """
    try:
        print('Bind {}'.format(str(LOCAL_PORT)))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LOCAL_ADDR, LOCAL_PORT))
    except socket.error as err:
        error("Bind failed", err)
        sock.close()
        sys.exit(0)
    # Listen
    try:
        sock.listen(10)
    except socket.error as err:
        error("Listen failed", err)
        sock.close()
        sys.exit(0)
    return sock

def exit_handler(signum, frame):
    """ Signal handler called with signal, exit script """
    print('Signal handler called with signal', signum)
    EXIT.set_status(True)

def main():
    """ Main function """
    print("Starting relay")
    new_socket = create_socket()
    bind_port(new_socket)
    signal(SIGINT, exit_handler)
    signal(SIGTERM, exit_handler)
    while not EXIT.get_status():
        if active_count() > MAX_THREADS:
            sleep(3)
            continue
        try:
            wrapper, _ = new_socket.accept()
            wrapper.setblocking(1)
        except socket.timeout:
            continue
        except socket.error:
            error()
            continue
        except TypeError:
            error()
            sys.exit(0)
        recv_thread = Thread(target=connection, args=(wrapper, ))
        recv_thread.start()
    new_socket.close()

EXIT = ExitStatus()
if __name__ == '__main__':
    main()







