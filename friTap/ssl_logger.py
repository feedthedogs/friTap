#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import frida
import tempfile
import os
import struct
import socket
import pprint
import signal
import time
import json
from .pcap import PCAP
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, LoggingEventHandler

try:
    import hexdump  # pylint: disable=g-import-not-at-top
except ImportError:
    print("Unable to import hexdump module!")
    pass


# here - where we are.
here = os.path.abspath(os.path.dirname(__file__))

# Names of all supported read functions:
SSL_READ = ["SSL_read", "wolfSSL_read", "readApplicationData", "NSS_read","Full_read"]
# Names of all supported write functions:
SSL_WRITE = ["SSL_write", "wolfSSL_write", "writeApplicationData", "NSS_write","Full_write"]

class SSL_Logger():

    def __init__(self, app, pcap_name=None, verbose=False, spawn=False, keylog=False, enable_spawn_gating=False, mobile=False, live=False, environment_file=None, debug_mode=False,full_capture=False, socket_trace=False, host=False, offsets=None, debug_output=False, experimental=False, anti_root=False, payload_modification=False,enable_default_fd=False):
        self.debug = debug_mode
        self.anti_root = anti_root
        self.pcap_name = pcap_name
        self.mobile = mobile
        self.debug_output = debug_output
        self.full_capture = full_capture
        self.target_app = app
        self.verbose = verbose
        self.spawn = spawn
        self.pcap_obj = None
        self.socket_trace = socket_trace
        self.keylog = keylog
        self.offsets = offsets
        self.offsets_data = None
        self.environment_file = environment_file
        self.host = host
        self.enable_spawn_gating = enable_spawn_gating
        self.live = live
        self.payload_modification = payload_modification
        self.enable_default_fd = enable_default_fd
        self.experimental = experimental

        self.tmpdir = None
        self.filename = ""
        self.startup = True

        self.process = None
        self.device = None
        self.keylog_file = None

        if frida.__version__ < "16":
            self.frida_agent_script = "_ssl_log_legacy.js"
        else:
            self.frida_agent_script = "_ssl_log.js"
        print("[***] loading frida script: " + self.frida_agent_script)

        self.keydump_Set = {*()}
        self.traced_Socket_Set = {*()}
        self.traced_scapy_socket_Set = {*()}
    
    
    def on_detach(self, reason):
        if reason != "application-requested":
            print(f"\n[*] Target process stopped: {reason}\n")
                    
        self.pcap_cleanup(self.full_capture,self.mobile,self.pcap_name)
        self.cleanup(self.live,self.socket_trace,self.full_capture,self.debug)


    def temp_fifo(self):
        self.tmpdir = tempfile.mkdtemp()
        self.filename = os.path.join(self.tmpdir, 'fritap_sharkfin')  # Temporary filename
        os.mkfifo(self.filename)  # Create FIFO
        try:
            return self.filename
        except OSError as e:
            print(f'Failed to create FIFO: {e}')
        

    def on_message(self, message, data):
        """Callback for errors and messages sent from Frida-injected JavaScript.
        Logs captured packet data received from JavaScript to the console and/or a
        pcap file. See https://www.frida.re/docs/messages/ for more detail on
        Frida's messages.
        Args:
        message: A dictionary containing the message "type" and other fields
            dependent on message type.
        data: The string of captured decrypted data.
        """
            
        if self.startup and message['payload'] == 'experimental':
            script.post({'type':'experimental', 'payload': self.experimental})

        if self.startup and message['payload'] == 'defaultFD':
            script.post({'type':'defaultFD', 'payload': self.enable_default_fd})
        
        if self.startup and message['payload'] == 'anti':
            script.post({'type':'antiroot', 'payload': self.anti_root})
            self.startup = False
            
        
        if message["type"] == "error":
            pprint.pprint(message)
            os.kill(os.getpid(), signal.SIGTERM)
            return
        
        p = message["payload"]
        if not "contentType" in p:
            return
        if p["contentType"] == "console":
            print("[*] " + p["console"])
        if self.debug or self.debug_output:
            if p["contentType"] == "console_dev" and p["console_dev"]:
                if len(p["console_dev"]) > 3:
                    print("[***] " + p["console_dev"])
        if self.verbose:
            if(p["contentType"] == "keylog") and self.keylog:
                if p["keylog"] not in self.keydump_Set:
                    print(p["keylog"])
                    self.keydump_Set.add(p["keylog"])
                    self.keylog_file.write(p["keylog"] + "\n")
                    self.keylog_file.flush()    
            elif not data or len(data) == 0:
                return
            else:
                src_addr = get_addr_string(p["src_addr"], p["ss_family"])
                dst_addr = get_addr_string(p["dst_addr"], p["ss_family"])
                
                if self.socket_trace == False and self.full_capture  == False:
                    print("SSL Session: " + str(p["ssl_session_id"]))
                if self.full_capture:
                    scapy_filter = PCAP.get_bpf_filter(src_addr,dst_addr)
                    self.traced_scapy_socket_Set.add(scapy_filter)
                if self.socket_trace:
                    display_filter = PCAP.get_display_filter(src_addr,dst_addr)
                    self.traced_Socket_Set.add(display_filter)
                    print("[socket_trace] %s:%d --> %s:%d" % (src_addr, p["src_port"], dst_addr, p["dst_port"]))
                else:
                    print("[%s] %s:%d --> %s:%d" % (p["function"], src_addr, p["src_port"], dst_addr, p["dst_port"]))
                    hexdump.hexdump(data)
                print()
        if self.pcap_name and p["contentType"] == "datalog" and self.full_capture == False:
            self.pcap_obj.log_plaintext_payload(p["ss_family"], p["function"], p["src_addr"],
                     p["src_port"], p["dst_addr"], p["dst_port"], data)
        if self.live and p["contentType"] == "datalog" and self.full_capture == False:
            try:
                self.pcap_obj.log_plaintext_payload(p["ss_family"], p["function"], p["src_addr"],
                         p["src_port"], p["dst_addr"], p["dst_port"], data)
            except (BrokenPipeError, IOError):
                self.process.detach()
                self.cleanup(self.live)

        if self.keylog and p["contentType"] == "keylog":
            if p["keylog"] not in self.keydump_Set:
                self.keylog_file.write(p["keylog"] + "\n")
                self.keylog_file.flush()
                self.keydump_Set.add(p["keylog"])
        
        if self.socket_trace or self.full_capture:
            if "src_addr" not in p:
                return
            
            src_addr = get_addr_string(p["src_addr"], p["ss_family"])
            dst_addr = get_addr_string(p["dst_addr"], p["ss_family"])
            if self.socket_trace:
                display_filter = PCAP.get_display_filter(src_addr,dst_addr)
                self.traced_Socket_Set.add(display_filter)
            else:
                scapy_filter = PCAP.get_bpf_filter(src_addr,dst_addr)
                self.traced_scapy_socket_Set.add(scapy_filter)
    

    def on_child_added(self, child):
        print(f"[*] Attached to child process with pid {child.pid}")
        self.instrument(self.device.attach(child.pid))
        self.device.resume(child.pid)


    def on_spawn_added(self, spawn):
        print(
            f"[*] Process spawned with pid {spawn.pid}. Name: {spawn.identifier}")
        self.instrument(self.device.attach(spawn.pid))
        self.device.resume(spawn.pid)
        

    def instrument(self, process):
        global script
        runtime="qjs"
        debug_port = 1337
        if self.debug:
            if frida.__version__ < "16":
                process.enable_debugger(debug_port)
            print("\n[!] running in debug mode")
            print(f"[!] Chrome Inspector server listening on port {debug_port}")
            print("[!] Open Chrome with chrome://inspect for debugging\n")
            runtime="v8"
        
        script_string = get_fritap_frida_script(self.frida_agent_script)
        

        if self.offsets_data is not None:
            print(f"[*] applying hooks at offset {self.offsets_data}")
            script_string = script_string.replace('"{OFFSETS}"', self.offsets_data)
            # might lead to a malformed package in recent frida versions
                    

        script = process.create_script(script_string, runtime=runtime)

        if self.debug and frida.__version__ >= "16":
            script.enable_debugger(debug_port)
        script.on("message", self.on_message)
        script.load()
        
        

        
        #script.post({'type':'readmod', 'payload': '0x440x410x53'})
        if self.payload_modification:
            class ModWatcher(FileSystemEventHandler):
                def __init__(self, process):
                    
                    self.process = process

                def on_any_event(self, event):
                    try:
                        if(event.event_type == "modified" and ("readmod" in event.src_path)):
                            with open("./readmod.bin", "rb") as f:
                                buffer = f.read()
                                script.post({'type':'readmod', 'payload': buffer.hex()})
                        elif(event.event_type == "modified" and ("writemod" in event.src_path)):
                            with open("./writemod.bin", "rb") as f:
                                buffer = f.read()
                                script.post({'type':'writemod', 'payload': buffer.hex()})
                    except RuntimeError as e:
                        print(e)
                
                

            print("Init watcher")
            event_handler = ModWatcher(process)
            
            observer = Observer()
            observer.schedule(event_handler, os.getcwd())
            observer.start()
    

    def start_fritap_session(self):

        if self.mobile:
            self.device = frida.get_usb_device()
        elif self.host:
            self.device = frida.get_device_manager().add_remote_device(self.host)
        else:
            self.device = frida.get_local_device()

        if self.offsets is not None:
            if os.path.exists(self.offsets):
                file = open(self.offsets, "r")
                self.offsets_data = file.read()
                file.close()
            else:
                try:
                    json.load(self.offsets)
                    self.offsets_data = self.offsets
                except ValueError as e:
                    print(f"Log error, defaulting to auto-detection: {e}")

        self.device.on("child_added", self.on_child_added)
        if self.enable_spawn_gating:
            self.device.enable_spawn_gating()
            self.device.on("spawn_added", self.on_spawn_added)
        if self.spawn:
            print("spawning "+ self.target_app)
            
            if self.pcap_name:
                self.pcap_obj =  PCAP(self.pcap_name,SSL_READ,SSL_WRITE,self.full_capture, self.mobile,self.debug)
                
            if self.mobile or self.host:
                pid = self.device.spawn(self.target_app)
            else:
                used_env = {}
                if self.environment_file:
                    with open(self.environment_file) as json_env_file:
                        used_env = json.load(json_env_file)
                pid = self.device.spawn(self.target_app.split(" "),env=used_env)
                self.device.resume(pid)
                time.sleep(1) # without it Java.perform silently fails
            self.process = self.device.attach(pid)
        else:
            if self.pcap_name:
                self.pcap_obj =  PCAP(self.pcap_name,SSL_READ,SSL_WRITE,self.full_capture, self.mobile,self.debug_mode)
            self.process = self.device.attach(int(self.target_app) if self.target_app.isnumeric() else self.target_app)

        if self.live:
            if self.pcap_name:
                print("[*] YOU ARE TRYING TO WRITE A PCAP AND HAVING A LIVE VIEW\nTHIS IS NOT SUPPORTED!\nWHEN YOU DO A LIVE VIEW YOU CAN SAFE YOUR CAPUTRE WIHT WIRESHARK.")
            fifo_file = self.temp_fifo()
            print(f'[*] friTap live view on Wireshark')
            print(f'[*] Created named pipe for Wireshark live view to {fifo_file}')
            print(
                f'[*] Now open this named pipe with Wireshark in another terminal: sudo wireshark -k -i {fifo_file}')
            print(f'[*] friTap will continue after the named pipe is ready....\n')
            self.pcap_obj =  PCAP(fifo_file,SSL_READ,SSL_WRITE,self.full_capture, self.mobile,self.debug)
            

        if self.keylog:
            self.keylog_file = open(self.keylog, "w")


        self.instrument(self.process)



        if self.pcap_name and self.full_capture:
            print(f'[*] Logging pcap to {self.pcap_name}')
        if self.pcap_name and self.full_capture == False:
            print(f'[*] Logging TLS plaintext as pcap to {self.pcap_name}')
        if self.keylog:
            print(f'[*] Logging keylog file to {self.keylog}')
            
        self.process.on('detached', self.on_detach)

        if self.spawn:
            self.device.resume(pid)

        return self.process
    

    def pcap_cleanup(self, is_full_capture, is_mobile, pcap_name):
        if is_full_capture and self.pcap_obj is not None:
                capture_type = "local"
                self.pcap_obj.full_capture_thread.join(2.0)
                if self.pcap_obj.full_capture_thread.is_alive() and is_mobile == False:
                    self.pcap_obj.full_capture_thread.socket.close()
                if self.pcap_obj.full_capture_thread.mobile_pid != -1:
                    capture_type = "mobile"
                    self.pcap_obj.full_capture_thread.mobile_pid.terminate()
                    self.pcap_obj.android_Instance.send_ctrlC_over_adb()
                    self.pcap_obj.android_Instance.pull_pcap_from_device()
                print(f"[*] full {capture_type} capture safed to _{pcap_name}")
                if self.keylog_file is None:
                    print(f"[*] remember that the full capture won't contain any decrypted TLS traffic.")
                else:
                    print(f"[*] remember that the full capture won't contain any decrypted TLS traffic. In order to decrypt it use the logged keys from {self.keylog_file.name}")
    

    def cleanup(self, live=False, socket_trace=False, full_capture=False, debug_output=False, debug=False):
        if live:
            os.unlink(self.filename)  # Remove file
            os.rmdir(self.tmpdir)  # Remove directory
        if type(socket_trace) is str:
            print(f"[*] Write traced sockets into {socket_trace}")
            self.write_socket_trace(socket_trace)
        if socket_trace == True:
            print("[*] Traced sockets")
            print(PCAP.get_filter_from_traced_sockets(self.traced_Socket_Set))
        
        if full_capture and len(self.traced_scapy_socket_Set) > 0:
            if debug_output or debug:
                print("[*] traced sockets: "+str(self.traced_scapy_socket_Set))

            self.pcap_obj.create_application_traffic_pcap(self.traced_scapy_socket_Set)
        elif full_capture and len(self.traced_scapy_socket_Set) < 1:
            print(f"[-] friTap was unable to indentify the used sockets.\n[-] The resulting PCAP will contain all trafic from the device.")
            
        print("\n\nThx for using friTap\nHave a great day\n")
        os._exit(0)

  
def get_addr_string(socket_addr,ss_family):
    if ss_family == "AF_INET":
        return  socket.inet_ntop(socket.AF_INET, struct.pack(">I", socket_addr))
    else: # this should only be AF_INET6
        raw_addr = bytes.fromhex(socket_addr)
        return socket.inet_ntop(socket.AF_INET6, struct.pack(">16s", raw_addr))
    

def get_fritap_frida_script(frida_agent_script):
    with open(os.path.join(here, frida_agent_script), encoding='utf8', newline='\n') as f:
            script_string = f.read()
            return script_string
            

def write_socket_trace(self, socket_trace_name):
    with open(socket_trace_name, 'a') as trace_file:
        trace_file.write(PCAP.get_filter_from_traced_sockets(self.traced_Socket_Set) + '\n')
   