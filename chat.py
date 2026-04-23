import threading
import json
import socket
import select
import time
import queue

import base64
import os

# CONFIG

TCP_PORT = 12487
UDP_PORT = 12487

# CONFIG


class Chat():
    def __init__(self, username):
        self.PORT = TCP_PORT 
        self.UDP_PORT = UDP_PORT
        self.DISCOVER_INTERVAL_SECONDS = 10

        self.username = username
        self.ip_chatting = None
        self.chatting_name = None
        self.menuextra = ""

        self.known_users = {}
        self.known_users_chats = {}

        self.stop_event = threading.Event()

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        self.my_ip = s.getsockname()[0]
        s.close()

        self.listener_sock = None
        self.listener_lock = threading.Lock()

        self.udp_listener_sock = None
        self.udp_listener_lock = threading.Lock()
        self.discover_thread = None
        self.discover_lock = threading.Lock()

        self.state = 0

        # --- File transfer: sender state ---
        # Each active send is tracked by target IP (one file per user at a time)
        self.send_states = {} # {ip: {"acked": set, "rwnd": int, "send_times": dict, "next_seq": int}}
        self.send_states_lock = threading.Lock()

        # --- File transfer: receiver state ---
        self.RECV_BUFFER_SIZE = 10 # parameterizable buffer size (in packets)
        self.recv_buffers = {} # {filename: {seq: body_bytes}}
        self.recv_expected_seq = {} # {filename: next seq to write}
        self.recv_files = {} # {filename: open file handle}
        self.recv_eof_seq = {} # {filename: seq number of EOF packet}

        # --- File transfer: incoming packet queue ---
        self.file_queue = queue.Queue()

    def _message_packet(self, message: str):
        return {
                "type": "MESSAGE",
                "PAYLOAD": message,
                "SENDER_NAME": self.username,
                "SENDER_IP": self.my_ip
            }

    def _send_packet(self, ip: str, packet: dict):
        if packet.get("type") == "MESSAGE":
            payload = str(packet.get("PAYLOAD", ""))
            payload_size = len(payload.encode("utf-8"))
            if payload_size > 2048:
                print(f"Message too large ({payload_size} bytes). Max allowed is 2048 bytes.")
                return False

        raw = json.dumps(packet)
        try:
            with socket.create_connection((ip, self.PORT), timeout=1.0) as s:
                s.sendall((raw + "\n").encode("utf-8"))
            return True
        except OSError:
            return False

    def _send_udp_packet(self, ip, packet):
        raw = json.dumps(packet).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(raw, (ip, self.UDP_PORT))


    def _send_ask_broadcast(self):
        packet = {
            "type": "ASK",
            "SENDER_IP": self.my_ip
        }
        raw = json.dumps(packet).encode("utf-8")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind(("", 0))
                sock.sendto(raw, ("<broadcast>", self.UDP_PORT))
            return True
        except OSError as e:
            print(f"broadcast failed: {e}")
            return False

    def _discover_worker(self):
        for _ in range(3):
            if self.stop_event.is_set():
                return
            self._send_ask_broadcast()
            time.sleep(0.2)

    def _auto_discover_loop(self):
        while not self.stop_event.is_set():
            self._discover_worker()
            # Wait in small steps so stop_event can terminate promptly.
            for _ in range(self.DISCOVER_INTERVAL_SECONDS * 10):
                if self.stop_event.is_set():
                    return
                time.sleep(0.1)

    def _start_discover_thread(self):
        with self.discover_lock:
            if self.discover_thread is not None and self.discover_thread.is_alive():
                return False
            self.discover_thread = threading.Thread(target=self._discover_worker, daemon=True)
            self.discover_thread.start()
            return True

    def _udp_listen_loop(self):
        port = self.UDP_PORT
        buffer_size = 8192

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        s.setblocking(0)

        with self.udp_listener_lock:
            self.udp_listener_sock = s

        try:
            while not self.stop_event.is_set():
                result = select.select([s], [], [], 1.0)  # timeout keeps loop stoppable
                if not result[0]:
                    continue

                msg, addr = result[0][0].recvfrom(buffer_size)
                if not msg:
                    continue

                sender_ip = addr[0]
                raw = msg.decode("utf-8", errors="replace").strip()
                if raw:
                    self._handle_received_packet(raw, sender_ip)
        finally:
            with self.udp_listener_lock:
                if self.udp_listener_sock is s:
                    self.udp_listener_sock = None
            s.close()

    def _handle_received_packet(self, packet: str, sender_ip: str = None):
        try:
            packet = json.loads(packet)
        except json.decoder.JSONDecodeError:
            return
        if packet["type"] == "ASK":
            pkt_sender_ip = packet.get("SENDER_IP")
            if pkt_sender_ip == self.my_ip:
                return  # ignore our own broadcast ASK
            reply = {
                "type": "REPLY",
                "RECEIVER_NAME": self.username,
                "RECEIVER_IP": self.my_ip
            }
            self._send_packet(packet["SENDER_IP"], reply)
        elif packet["type"] == "REPLY":
            receiver_name = packet["RECEIVER_NAME"]
            receiver_ip = packet["RECEIVER_IP"]
            if receiver_ip == self.my_ip:
                return
            if receiver_ip not in self.known_users:
                self.known_users[receiver_ip] = receiver_name
                self.known_users_chats.setdefault(receiver_ip, [])
                print(f'Discovered {receiver_name} @ {receiver_ip}')
            elif self.known_users[receiver_ip] != receiver_name:
                print(f'Discovered {receiver_name} @ {receiver_ip}, [Address was previously used by {self.known_users[receiver_ip]}]')
                self.known_users[receiver_ip] = receiver_name
                self.known_users_chats.setdefault(receiver_ip, [])
            else:
                pass

            if self.state == 0:
                self._clear_window()
                self._render_menu()
                print("> ", end="", flush=True)

        elif packet["type"] == "MESSAGE":
            pkt_sender_ip = packet["SENDER_IP"]
            sender_name = packet["SENDER_NAME"]
            payload = packet["PAYLOAD"]
            self.known_users_chats.setdefault(pkt_sender_ip, []).append((sender_name, payload))
            if self.state == 1:
                self._clear_window()
                self._render_chat()

        elif packet["type"] == "File" or packet["type"] == "ACK":
            # Put into queue for the file worker thread to handle
            self.file_queue.put((packet, sender_ip))

        else:
            pass

    def _file_worker_loop(self):
        """Single thread that processes all incoming File and ACK packets from the queue."""
        while not self.stop_event.is_set():
            try:
                packet, sender_ip = self.file_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if packet["type"] == "File":
                self._handle_file_packet(packet, sender_ip)
            elif packet["type"] == "ACK":
                self._handle_ack_packet(packet, sender_ip)

    def _handle_file_packet(self, packet, sender_ip):
        filename = packet["NAME"]
        seq = packet["SEQ"]
        eof = packet.get("EOF", False)
        body = base64.b64decode(packet["BODY"])

        # Initialize receive state for this file if first time seeing it
        if filename not in self.recv_buffers:
            self.recv_buffers[filename] = {}
            self.recv_expected_seq[filename] = 1
            os.makedirs("received", exist_ok=True)
            self.recv_files[filename] = open(os.path.join("received", filename), "wb")
            self._add_info_to_chat(sender_ip, f"Receiving file: {filename}")

        # Remember which SEQ is the last one
        if eof:
            self.recv_eof_seq[filename] = seq

        # Store packet in buffer (handles out-of-order and duplicates)
        self.recv_buffers[filename][seq] = body

        # Write contiguous packets starting from expected_seq
        while self.recv_expected_seq[filename] in self.recv_buffers[filename]:
            expected = self.recv_expected_seq[filename]
            self.recv_files[filename].write(self.recv_buffers[filename].pop(expected))
            self.recv_expected_seq[filename] += 1

        # Calculate RWND
        rwnd = self.RECV_BUFFER_SIZE - len(self.recv_buffers[filename])
        if rwnd < 0:
            rwnd = 0

        # Send ACK back (sender identifies transfer by our IP from UDP header)
        ack = {
            "type": "ACK",
            "SEQ": seq,
            "RWND": rwnd
        }
        self._send_udp_packet(sender_ip, ack)

        # Check if transfer is complete
        if filename in self.recv_eof_seq:
            if self.recv_expected_seq[filename] > self.recv_eof_seq[filename]:
                self.recv_files[filename].close()
                del self.recv_files[filename]
                del self.recv_buffers[filename]
                del self.recv_expected_seq[filename]
                del self.recv_eof_seq[filename]
                self._add_info_to_chat(sender_ip, f"File {filename} received successfully! Saved to received/{filename}")

    def _handle_ack_packet(self, packet, sender_ip):
        ack_seq = packet["SEQ"]
        rwnd = packet["RWND"]

        with self.send_states_lock:
            if sender_ip not in self.send_states:
                return
            state = self.send_states[sender_ip]

        with state["lock"]:
            state["acked"].add(ack_seq)
            state["rwnd"] = rwnd
            if ack_seq in state["send_times"]:
                del state["send_times"][ack_seq]

    def _tcp_listen_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(("", self.PORT))
            server_sock.listen()
            server_sock.settimeout(1.0)

            with self.listener_lock:
                self.listener_sock = server_sock

            while not self.stop_event.is_set():
                try:
                    conn, addr = server_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                with conn:
                    chunks = []
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                        if b"\n" in data:
                            break

                    raw = b"".join(chunks).decode("utf-8", errors="replace").strip()
                    if raw:
                        self._handle_received_packet(raw)

            with self.listener_lock:
                if self.listener_sock is server_sock:
                    self.listener_sock = None


    def _stop_listener_socket(self):
        with self.listener_lock:
            sock = self.listener_sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _stop_udp_listener_socket(self):
        with self.udp_listener_lock:
            sock = self.udp_listener_sock
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _clear_window(self):
        print("\x1b[2J\x1b[H", end="")

    def _render_menu(self):
        print("487 Chat App")
        print("Write '\quit' to quit")
        print("New users are discovered automatically every 10 seconds.")
        print("Write '\discover' to manually discover new users around you.")
        print("Write the name of a user to chat with them.")
        print(self.menuextra)
        print()
        print("Users you already know:")
        print(self.known_users)

    def _render_chat(self):
        print("Chatting with", self.chatting_name)
        print("Type '\\menu' to return to menu, '\\quit' to quit, '\\sendfile <path>' to send a file.")
        print()
        if self.ip_chatting is None:
            return

        for sender_name, message in self.known_users_chats.get(self.ip_chatting, []):
            print(f"{sender_name}: {message}")

    def _add_info_to_chat(self, ip, message):
        """Add an info message to the chat history with a user and re-render if currently viewing."""
        self.known_users_chats.setdefault(ip, []).append(("[info]", message))
        if self.state == 1 and self.ip_chatting == ip:
            self._clear_window()
            self._render_chat()

    def _find_ip(self, users, username):
        return next((ip for ip, user in users.items() if user == username), None)

    def _create_file_packets(self, filepath):
        filename = os.path.basename(filepath)
        chunk_size = 1500

        with open(filepath, "rb") as f:
            data = f.read()

        packets = []
        seq = 1
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i+chunk_size]
            is_last = (i + chunk_size >= len(data))
            packet = {
                "type": "File",
                "NAME": filename,
                "SEQ": seq,
                "EOF": is_last,
                "BODY": base64.b64encode(chunk).decode("ascii")
            }
            packets.append(packet)
            seq += 1
        return packets

    def _send_file(self, ip, filepath):
        packets = self._create_file_packets(filepath)
        total = len(packets)
        filename = os.path.basename(filepath)

        # Create per-file send state
        state = {
            "acked": set(),
            "rwnd": 1,
            "send_times": {},
            "next_seq": 1,
            "lock": threading.Lock()
        }

        with self.send_states_lock:
            self.send_states[ip] = state

        # Add a progress message to chat history and remember its index so we can update it
        chat_list = self.known_users_chats.setdefault(ip, [])
        progress_idx = len(chat_list)
        chat_list.append(("[info]", f"Sending {filename}: 0/{total} packets"))
        if self.state == 1 and self.ip_chatting == ip:
            self._clear_window()
            self._render_chat()

        last_acked_count = 0

        while True:
            with state["lock"]:
                acked_count = len(state["acked"])
                if acked_count >= total:
                    break

                # Retransmit packets not ACKed within 1 second
                now = time.time()
                for seq, sent_time in list(state["send_times"].items()):
                    if seq not in state["acked"] and now - sent_time > 1.0:
                        self._send_udp_packet(ip, packets[seq - 1])
                        state["send_times"][seq] = now

                # Send new packets if window allows
                in_flight = (state["next_seq"] - 1) - acked_count
                while in_flight < state["rwnd"] and state["next_seq"] <= total:
                    self._send_udp_packet(ip, packets[state["next_seq"] - 1])
                    state["send_times"][state["next_seq"]] = time.time()
                    state["next_seq"] += 1
                    in_flight += 1

            # Update progress message when acked count changes
            if acked_count != last_acked_count:
                last_acked_count = acked_count
                chat_list[progress_idx] = ("[info]", f"Sending {filename}: {acked_count}/{total} packets")
                if self.state == 1 and self.ip_chatting == ip:
                    self._clear_window()
                    self._render_chat()

            time.sleep(0.01)

        with self.send_states_lock:
            del self.send_states[ip]

        # Update the progress line to show completion
        chat_list[progress_idx] = ("[info]", f"Sent {filename}: {total}/{total} packets")
        if self.state == 1 and self.ip_chatting == ip:
            self._clear_window()
            self._render_chat()


    def run(self):
        tcp_listen_thread = None
        udp_listen_thread = None
        auto_discover_thread = None
        file_worker_thread = None
        try:
            self._clear_window()
            self.state = 0

            tcp_listen_thread = threading.Thread(target=self._tcp_listen_loop, daemon=True)
            tcp_listen_thread.start()

            udp_listen_thread = threading.Thread(target=self._udp_listen_loop, daemon=True)
            udp_listen_thread.start()

            auto_discover_thread = threading.Thread(target=self._auto_discover_loop, daemon=True)
            auto_discover_thread.start()

            file_worker_thread = threading.Thread(target=self._file_worker_loop, daemon=True)
            file_worker_thread.start()

            while True:
                if self.state == 0:
                    self._clear_window()
                    self._render_menu()
                elif self.state == 1:
                    self._clear_window()
                    self._render_chat()

                cmd = input("> ").strip()
                self.menuextra = ""

                if self.state == 0:
                    if cmd == "\quit":
                        break
                    elif cmd == "\discover":
                        started = self._start_discover_thread()
                        if not started:
                            self.menuextra = "Discovery is already running."
                    else:
                        self.ip_chatting = self._find_ip(self.known_users, cmd)
                        if self.ip_chatting is not None:
                            self.state = 1
                            self.chatting_name = cmd
                            self.known_users_chats.setdefault(self.ip_chatting, [])
                        else:
                            self.menuextra = "No user with name " + cmd
                else:
                    if cmd == "\quit":
                        break
                    elif cmd == "\menu":
                        self.state = 0
                    elif cmd.split()[0] == "\sendfile":
                        parts = cmd.split(maxsplit=1)
                        if len(parts) < 2:
                            self._add_info_to_chat(self.ip_chatting, "Usage: \\sendfile <filepath>")
                        else:
                            path = parts[1]
                            if not os.path.isfile(path):
                                self._add_info_to_chat(self.ip_chatting, f"File not found: {path}")
                            else:
                                threading.Thread(
                                    target=self._send_file,
                                    args=(self.ip_chatting, path),
                                    daemon=True
                                ).start()
                    else:
                        sent = self._send_packet(self.ip_chatting, self._message_packet(cmd))
                        if sent:
                            self.known_users_chats.setdefault(self.ip_chatting, []).append((self.username, cmd))
        finally:
            self.stop_event.set()
            self._stop_listener_socket()
            self._stop_udp_listener_socket()
            if tcp_listen_thread is not None:
                tcp_listen_thread.join(timeout=2)
            if udp_listen_thread is not None:
                udp_listen_thread.join(timeout=2)
            if auto_discover_thread is not None:
                auto_discover_thread.join(timeout=2)
            if file_worker_thread is not None:
                file_worker_thread.join(timeout=2)

if __name__ == "__main__":
    print("\x1b[2J\x1b[H", end="")
    username = ""
    while not username:
        username = input("Enter username: ").strip()
        if not username:
            print("Username cannot be empty.")
    chatapp = Chat(username)
    chatapp.run()
