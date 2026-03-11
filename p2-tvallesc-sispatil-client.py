import socket
import argparse
import sys
import select
import signal

# parse args
parser = argparse.ArgumentParser()
parser.add_argument('--id', required=True)
parser.add_argument('--port', required=True, type=int)
parser.add_argument('--server', required=True)
args = parser.parse_args()

client_id = args.id
client_port = args.port
server_ip, server_port = args.server.split(':')
server_port = int(server_port)
client_ip = socket.gethostbyname(socket.gethostname())

# client state
state = 'IDLE'
peer_id = ''
peer_ip_val = ''
peer_port_val = 0
listen_sock = None
peer_sock = None

# handle sigint
def handle_sigint(sig, frame):
    if listen_sock:
        try:
            listen_sock.close()
        except Exception:
            pass
    if peer_sock:
        try:
            peer_sock.close()
        except Exception:
            pass
    sys.exit(0)

signal.signal(signal.SIGINT, handle_sigint)

# helpers

#parse raw in wire message and format it to headers obj{}
def parse_message(raw):
    lines = raw.split('\r\n')
    msg_type = lines[0].strip()
    headers = {}

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        if ': ' in line:
            key, val = line.split(': ', 1)
            headers[key] = val
        elif ':' in line:
            key, val = line.split(':', 1)
            headers[key] = val.lstrip()

    return msg_type, headers

# build wire format string from type and headers dic
def build_message(msg_type, headers):
    msg = msg_type + '\r\n'

    for key, val in headers.items():
        msg += f'{key}: {val}\r\n'
    
    msg += '\r\n'
    return msg

#recv helper
def recv_message(sock):
    try:
        data = sock.recv(4096)
        if not data:
            return ''
        return data.decode()
    except socket.error:
        return ''

# main chat loop, reading = true means we wait for the peer to send something
def chat_loop(reading):
    global peer_sock

    while True:
        try:
            if reading: # reading mode, we are waiting
                readable, _, _ = select.select([peer_sock], [], [])
                raw = recv_message(peer_sock)

                if not raw:
                    peer_sock.close()
                    sys.exit(0)

                msg_type, _ = parse_message(raw)

                if msg_type == 'QUIT':
                    peer_sock.close()
                    sys.exit(0)
                    
                print(raw.rstrip('\r\n'))
                reading = False
            else:
                # writing mode- watch stdin for input, peer_sock for unexpected close
                readable, _, _ = select.select([peer_sock, sys.stdin], [], [])

                for fd in readable:
                    if fd is peer_sock:
                        # unexpected data, so we quit
                        raw = recv_message(peer_sock)

                        if not raw:
                            peer_sock.close()
                            sys.exit(0)

                        msg_type, _ = parse_message(raw)

                        if msg_type == 'QUIT':
                            peer_sock.close()
                            sys.exit(0)

                    elif fd is sys.stdin:
                        line = sys.stdin.readline()

                        if not line:  # EOF (Ctrl-D)
                            peer_sock.close()
                            sys.exit(0)

                        line = line.strip()

                        if line == '/quit':
                            quit_msg = build_message('QUIT', {})
                            peer_sock.sendall(quit_msg.encode())
                            peer_sock.close()
                            sys.exit(0)

                        peer_sock.sendall((line + '\r\n').encode())
                        reading = True
                        break
        except socket.error as e:
            print(f'socket error in chat: {e}', file=sys.stderr)

            if peer_sock:
                try:
                    peer_sock.close()
                except Exception:
                    pass
                
            sys.exit(1)

# called when client 1 accepts an incoming chat connection from client 2
def handle_incoming_chat(conn_sock):
    global peer_id, peer_ip_val, peer_port_val, listen_sock, peer_sock, state
    raw = recv_message(conn_sock)

    if not raw:
        conn_sock.close()
        sys.exit(0)

    #grab info from headers
    msg_type, headers = parse_message(raw)
    peer_id = headers.get('clientID', '')
    peer_ip_val = headers.get('IP', '')
    port_str = headers.get('Port', '0').strip()
    peer_port_val = int(port_str) if port_str else 0

    if listen_sock:
        try:
            listen_sock.close()
        except Exception:
            pass
        listen_sock = None
    
    peer_sock = conn_sock

    print(f'>Incoming chat request from {peer_id} {peer_ip_val}:{peer_port_val}')
    
    state = 'CHAT'
    
    chat_loop(reading=True)

# primary wait loop where client 1 waits for client 2
def wait_loop():
    global listen_sock

    while True:
        try:
            readable, _, _ = select.select([listen_sock, sys.stdin], [], [])
            
            for fd in readable:
                if fd is sys.stdin:
                    line = sys.stdin.readline().strip()

                    if line == '/quit':
                        if listen_sock:
                            listen_sock.close()
                        sys.exit(0)

                    # other commands in WAIT state are ignored
                elif fd is listen_sock:
                    conn, addr = listen_sock.accept()
                    handle_incoming_chat(conn)
                    return
                
        except socket.error as e:
            print(f'socket error in wait: {e}', file=sys.stderr)

            if listen_sock:
                try:
                    listen_sock.close()
                except Exception:
                    pass

            sys.exit(1)

# enter wait state for client 1
def enter_wait():
    global listen_sock, state

    try:
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listen_sock.bind(('', client_port))
        listen_sock.listen(1)
    except socket.error as e:
        print(f'socket error entering wait: {e}', file=sys.stderr)

        sys.exit(1)
        
    state = 'WAIT'
    wait_loop()

# where client 2 connects to peer and send CHAT init message
def enter_chat_as_initiator():
    """Client #2: connect to peer and send CHAT initiation message."""
    global peer_sock, state

    try:
        peer_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        peer_sock.connect((peer_ip_val, peer_port_val))

        msg = build_message('CHAT', {
            'clientID': client_id,
            'IP': client_ip,
            'Port': str(client_port)
        })

        peer_sock.sendall(msg.encode())
    except socket.error as e:
        print(f'socket error connecting to peer: {e}', file=sys.stderr)

        if peer_sock:
            try:
                peer_sock.close()
            except Exception:
                pass

        sys.exit(1)

    state = 'CHAT'
    chat_loop(reading=False)

# startup and main loop
print(f'>{client_id} running on {client_ip}:{client_port}')

while True:
    try:
        command = input().strip()

        if command == '/id':
            print(f'>{client_id}')

        elif command == '/register':
            # send REGISTER, receive REGACK, update state
            try:
                msg = build_message('REGISTER', {
                    'clientID': client_id,
                    'IP': client_ip,
                    'Port': str(client_port)
                })

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

                sock.connect((server_ip, server_port))
                sock.sendall(msg.encode())

                raw = recv_message(sock)
                sock.close()

                if raw:
                    state = 'REGISTERED'
                
            except socket.error as e:
                print(f'socket error: {e}', file=sys.stderr)

        elif command == '/bridge':
            # send BRIDGE, receive BRIDGEACK, determine role
            try:
                msg = build_message('BRIDGE', {'clientID': client_id})

                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((server_ip, server_port))
                sock.sendall(msg.encode())

                raw = recv_message(sock)

                sock.close()
                
                if raw:
                    msg_type, headers = parse_message(raw)
                    peer_id = headers.get('clientID', '')
                    peer_ip_val = headers.get('IP', '').strip()
                    port_str = headers.get('Port', '').strip()
                    
                    peer_port_val = int(port_str) if port_str else 0

                    if peer_ip_val:
                        # client #2 wait for /chat command
                        state = 'REGISTERED'
                    else:
                        enter_wait()
                        
            except socket.error as e:
                print(f'socket error: {e}', file=sys.stderr)

        elif command == '/chat':
            # client #2 explicitly inits chat with peer
            if peer_ip_val:
                enter_chat_as_initiator()

        elif command == '/quit':
            if listen_sock:
                try:
                    listen_sock.close()
                except Exception:
                    pass
            if peer_sock:
                try:
                    peer_sock.close()
                except Exception:
                    pass
            sys.exit(0)

    except EOFError:
        if listen_sock:
            try:
                listen_sock.close()
            except Exception:
                pass
        if peer_sock:
            try:
                peer_sock.close()
            except Exception:
                pass
        sys.exit(0)
