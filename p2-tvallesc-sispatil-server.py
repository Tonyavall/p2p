import socket
import argparse
import sys
import select
import signal
import os

# init startup args
parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True, type=int)
args = parser.parse_args()

server_port = args.port
server_ip = socket.gethostbyname(socket.gethostname())

# state
server_socket = None
clients = []  # in mem list of {clientID, IP, Port}
CLIENTS_FILE = 'clients.txt'

# helpers
def handle_sigint(sig, frame):
    if server_socket:
        try:
            server_socket.close()
        except Exception:
            pass
    sys.exit(0)

signal.signal(signal.SIGINT, handle_sigint)

# parse on the wire format msg into (type, headers_dict)
def parse_message(raw):
    lines = raw.split('\r\n')
    msg_type = lines[0].strip() # /register, etc
    headers = {}

    for line in lines[1:]: # grab everything after the req type
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

# build wire format string from type and headers dict
def build_message(msg_type, headers):
    msg = msg_type + '\r\n'

    for key, val in headers.items():
        msg += f'{key}: {val}\r\n'
    
    msg += '\r\n' # for double carirage return
    return msg

#recv helper, returns empty str on tcp fin or err
def recv_message(sock):
    try:
        data = sock.recv(4096)

        if not data:
            return ''
        return data.decode()
    except socket.error:
        return ''

# client storage, append client records to client.txt and update the memory lsit
def store_client(client_id, ip, port):
    record = {'clientID': client_id, 'IP': ip, 'Port': str(port)}
    clients.append(record)

    try:
        with open(CLIENTS_FILE, 'a') as f:
            f.write(f'{client_id} {ip} {port}\n')
    except OSError as e:
        print(f'file error: {e}', file=sys.stderr)

# client loader that reads clients.txt and return a list of dicts or [] if missing
def load_clients():
    result = []

    if not os.path.exists(CLIENTS_FILE):
        return result
    try:
        with open(CLIENTS_FILE, 'r') as f:
            for line in f:
                line = line.strip()

                if not line: continue

                parts = line.split()
                
                if len(parts) == 3: # make sure that it's not malformed
                    result.append({
                        'clientID': parts[0],
                        'IP': parts[1],
                        'Port': parts[2]
                    })
    except OSError as e:
        print(f'file error: {e}', file=sys.stderr)

    return result

# print all registered clients
def print_info():
    stored = load_clients()

    for record in stored:
        print(f'>{record["clientID"]} {record["IP"]}:{record["Port"]}')

# handle register request- store client, send regack and close the conn
def handle_register(conn, addr, headers):
    client_id = headers.get('clientID', '')
    ip = headers.get('IP', addr[0])
    port = headers.get('Port', '0')
    
    store_client(client_id, ip, port)
    print(f'REGISTER: {client_id} from {ip}:{port} received')

    response = build_message('REGACK', {
        'clientID': client_id,
        'IP': ip,
        'Port': port,
        'Status': 'registered'
    })
    
    try:
        conn.sendall(response.encode())
    except socket.error as e:
        print(f'socket error sending REGACK: {e}', file=sys.stderr)
    finally:
        conn.close()

# handle bridge req
def handle_bridge(conn, headers):
    req_id = headers.get('clientID', '')
    stored = load_clients()

    # find peer that is not the curr client
    peer = None
    req_record = None

    for record in stored:
        if record['clientID'] == req_id:
            req_record = record
        else:
            peer = record

    if peer:
        # Print BRIDGE message with both peer and requester info
        req_ip = req_record['IP'] if req_record else ''
        req_port = req_record['Port'] if req_record else ''

        print(f'BRIDGE: {peer["clientID"]} {peer["IP"]}:{peer["Port"]} '
              f'{req_id} {req_ip}:{req_port}')
        
        response = build_message('BRIDGEACK', {
            'clientID': peer['clientID'],
            'IP': peer['IP'],
            'Port': peer['Port']
        })

    else:
        # No peer registered yet so we return empty values
        print(f'BRIDGE: {req_id}')

        response = build_message('BRIDGEACK', {
            'clientID': '',
            'IP': '',
            'Port': ''
        })

    try:
        conn.sendall(response.encode())
    except socket.error as e:
        print(f'socket error sending BRIDGEACK: {e}', file=sys.stderr)
    finally:
        conn.close()

# handle client connection
def handle_client(conn, addr):
    try:
        raw = recv_message(conn)

        if not raw:
            conn.close()
            return
        
        msg_type, headers = parse_message(raw)
        
        if msg_type == 'REGISTER':
            handle_register(conn, addr, headers)
        elif msg_type == 'BRIDGE':
            handle_bridge(conn, headers)
        else:
            print('Malformed incoming message', file=sys.stderr)
            conn.close()
            server_socket.close()
            sys.exit(1)

    except socket.error as e:
        print(f'socket error handling client: {e}', file=sys.stderr)

        try:
            conn.close()
        except Exception:
            pass

# main acc loop, select on server_socket and dispatch connections and commands
def accept_loop():
    while True:
        try:
            # watch tcp conn with peer and the keyboard
            readable, _, _ = select.select([server_socket, sys.stdin], [], [])

            for fd in readable: # for each file descriptor
                if fd is sys.stdin: # if the file desc is keyboard input from terminal
                    line = sys.stdin.readline().strip()

                    if line == '/info':
                        print_info()
                elif fd is server_socket: # otherwise data arrived over network and we handle each cliennt
                    conn, addr = server_socket.accept()
                    handle_client(conn, addr)

        except socket.error as e:
            print(f'socket error in accept loop: {e}', file=sys.stderr)
            server_socket.close()
            sys.exit(1)

#clear clients.txt on startup
try:
    with open(CLIENTS_FILE, 'w') as f:
        pass  # truncate to empty
except OSError:
    pass

#load existing  clients from file
clients = load_clients()

# create and bind listening socket
try:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) #ipv4, tcp
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) #apply opt at socket level, allow addr reuse
    server_socket.bind(('', server_port))
    server_socket.listen()
except socket.error as e:
    print(f'socket error starting server: {e}', file=sys.stderr)
    sys.exit(1)

print(f'>Server listening on {server_ip}:{server_port}')

accept_loop()