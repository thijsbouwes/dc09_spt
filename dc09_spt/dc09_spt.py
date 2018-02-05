# ----------------------------
# Dialler class
# (c 2018 van Ovost Automatisering b.v.
# Author : Jacq. van Ovost
# ----------------------------
from dc09_msg import *
from dc03_msg import *
from dc05_msg import *
import socket
import time
import threading
from collections import deque


class dc09_spt():
    """
    Handle the basic tasks of SPT (Secured Premises Transciever)

    Copyright (c) 2018  van Ovost Automatisering b.v.

    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    you may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
    """

    def __init__(self,  account, receiver=None,  line=None):
        """
        Define a basic dialler (SPT Secure Premises Transceiver)
        
        parameters
            account
                Account number to be used. 
                Most receivers expect a numeric string of 4 to 8 digits
            receiver
                an optional integer to be used as receiver number in the block header
            line
                an optional integer to be used as line number in the block header
        """
        self.account = account
        self.receiver = receiver
        self.line = line
        self.tpaths = {
            'main': {
                'primary': {
                    'path': None,
                    'ok':   0
                },  
                'secondary': {
                    'path': None,
                    'ok':   0
                }  
            }, 
            'back-up': {
                'primary': {
                    'path': None,
                    'ok':   0
                },   
                'secondary': {
                    'path': None,
                    'ok':   0
                }  
            }, 
        }
        self.tpaths_lock = threading.Lock()
        self.backup_prim = None
        self.backup_sec = None
        self.main_ok = 0
        self.backup_ok = 0
        self.main_poll = None
        self.backup_poll = None
        self.msg_nr = 0
        self.queue = deque()
        self.queuelock = threading.Lock()
        self.running = 0
        self.poll = None
        self.send = None
        self.counter = 0
        self.counterlock = threading.Lock()
        self.routines = []
        self.routines_changed = 0
# ---------------------
# configure transmission paths
# ---------------------
    def set_path(self, mb,  pb,  host,  port,  account=None,  key=None,  receiver=None,  line=None):
        """
        Define the transmission path 
        
        parameters
            main/back-up
                value 'main' or 'back-up'
            primary/secondary
                value 'primary' or 'secondary'
            host
                IP address or DNS name of receiver
            port
                Port number to be used at this receiver
            account
                Optional different account number to be used for this path. 
                Most receivers expect a numeric string of 4 to 8 digits
            key
                Optional encryption key.
                This key should be byte string of 16 or 32 bytes                
            receiver
                an optional integer to be used as receiver number in the block header
            line
                an optional integer to be used as line number in the block header
        note
            The routing of the back-up path to use the secondary network adapter has to be done
            in the operating system. The decision which adapter to use is made at the moment of routing.
        """
        if account != None:
            acc = account
            if self.account == None:
                self.account = account
        else:
            acc = self.account
        if receiver != None:
            rec = receiver
            if self.receiver == None:
                self.receiver = receiver
        else:
            rec = self.receiver
        if line != None:
            lin = line
            if self.line == None:
                self.line = lin
        else:
            lin = self.line
        self.tpaths_lock.acquire()
        self.tpaths[mb][pb]['path'] = TransPath(host,  port,  acc,  key,  rec,  lin)
        self.tpaths[mb][pb]['ok'] = 0
        self.tpaths_lock.release()
            
    def del_path(self, mb,  pb):
        """
        Remove a transmission path
        
        parameters
            main/back-up
                value 'main' or 'back-up'
            primary/secondary
                value 'primary' or 'secondary'
        """
        self.tpaths_lock.acquire()
        self.tpaths[mb][pb]['path'] = None
        self.tpaths_lock.release()
                
    def start_poll(self,  main,  backup=None,  retry_delay=5,  ok_msg=None,  fail_msg=None):
        """
        Start the automatic polling to the receiver(s)
        
        parameters
            main
                Polling interval of the main path
            backup
                Optional polling interval of the back-up path
            ok_msg  
                Optional map with message to sent on poll restore
            fail_msg
                optional map with message to send when poll fails
        """
        if self.poll == None:
            self.poll = poll_thread(self.account, self.receiver, self.line, self.tpaths, self.tpaths_lock, retry_delay,  self)
            self.poll.set_poll(main,  backup,   ok_msg,  fail_msg)
            self.poll_active = 1
            self.poll.start()
        else:
            self.poll.set_poll(main,  backup,  ok_msg,  fail_msg)

    def stop_poll(self):
        if self.poll != None and self.poll.active() == 1:
            self.poll.stop()
            self.poll_active = -1
            self.poll.join()
            self.poll_active = 0
            self.poll = None

    def start_routine(self,  list):
        if self.poll == None:
            if len(list):
                self.poll = poll_thread(self.account, self.receiver, self.line, self.tpaths, self.tpaths_lock, 5.0,  self)
                self.poll.set_routines(list)
                self.poll_active = 1
                self.poll.start()
        else:
            self.poll.set_routines(list)
            if len(list) == 0:
                if self.poll.active() == 2:
                    self.poll.stop()
                    self.poll_active = -1
                    self.poll.join()
                    self.poll_active = 0
                    self.poll = None

    def send_msg(self,  type,  param):
        """
        Schedule a message for sending to the receiver
        
        parameters
            type    
                type of message to send
                current implemented is :
                    'SIA' or 'SIA-DCS' for sending a message with a SIA-DC03 payload
                    'CID' or 'ADM-CID' for sending a message with a SIA-DC05 payload
            param  
                a map of key value pairs defining the message content.
                for a description of possible values see the documentation of the payload
        
        note
            this method can be called from more than one thread
        """
        self.counterlock.acquire()
        self.msg_nr += 1
        self.counter += 1
        self.counterlock.release()
        if self.msg_nr > 9999:
            self.msg_nr = 1
        if type == 'SIA' or type == 'SIA-DCS':
            msg = dc03_msg.dc03event(self.account,  param)
            dc09type = 'SIA-DCS'
        if type == 'CID' or type == 'ADM-CID':
            msg = dc05_msg.dc05event(self.account,  param)
            dc09type = 'ADM-CID'
        extra = dc09_msg.dc09_extra(param)
        if extra != None:
            msg = msg + extra
        tup = self.msg_nr,  dc09type,  msg
        print(tup)
        self.queuelock.acquire()
        self.queue.append(tup)
        self.queuelock.release()
        if self.send != None and self.send.active() == 0:
            self.send.join()
            self.send = None
        if self.send == None:
            self.send = event_thread(self.account, self.receiver, self.line, self.queue,  self.queuelock,  self.tpaths, self.tpaths_lock)
            self.send.start()
    
    def state(self):
        ret = {'msgs queued': len(self.queue), 'msgs sent': self.counter}
        for mb in ('main',  'back-up'):
            for ps in ('primary',  'secondary'):
                if self.tpaths[mb][ps]['path'] != None:
                    ret[mb + ' ' + ps + ' path ok'] = self.tpaths[mb][ps]['ok']
        if self.poll != None:
            ret['poll active'] = self.poll.active()
            ret['poll count'] = self.poll.count()
        if self.send != None:
            ret['send active'] = self.send.active()
        return ret
    
        
class TransPath:
    """
    Handle the basic tasks for establishing and maintaining a transmit path
    """
    def __init__(self,  host,  port,  account,  key=None,  receiver=None,  line=None,  timeout=5.0):
        self.path_ok = 0
        self.host = host
        self.port = port
        self.offset = 0
        self.timeout = timeout
        self.dc09 = dc09_msg(account,  key,  receiver,  line)
# -----------------
# send a poll and check result
# ----------------
    def poll(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.host, self.port))
            self.dc09.set_offset(self.offset)
            msg = str.encode(self.dc09.dc09poll())
            s.send(msg)
            antw=s.recv(1024)
            s.close()
            answer = self.dc09.dc09answer(0, antw.decode())
            print(0,  answer)
            self.offset = answer[1]
            if  answer[0] == 'ACK':
                self.path_ok = 1
                return 1
            else:
                self.path_ok = 0
                return 0
        except Exception as e:
            self.path_ok = 0
            print (0,  e)
            return 0
# -----------------
# send a message and check result
# ----------------
    def message(self,  msg_nr,  type,  mess):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.host, self.port))
            self.dc09.set_offset(self.offset)
            msg = str.encode(self.dc09.dc09block(msg_nr, type,  mess))
            s.send(msg)
            antw=s.recv(1024)
            s.close()
            answer = self.dc09.dc09answer(msg_nr, antw.decode())
            print(msg_nr,  answer)
            self.offset = answer[1]
            if answer[0] == 'ACK':
                self.path_ok = 1
                return 1
            else:
                self.path_ok = 0
                return 0
        except Exception as e:
            self.path_ok = 0
            print (msg_nr,  e)
            return 0
# --------------------------
# return path status
# ----------------------
    def ok(self):
        return self.path_ok
    
    
class poll_thread(threading.Thread):
    """
    Handle the polling tasks of SPT (Secured Premises Transciever)
    extra task is handle the routine events if any
    """
    def __init__(self,  account, receiver,  line,  paths,  pathlock,  retry_delay,  parent):
        """
        Create polling thread 
        
        parameters
            main
                polling interval in seconds of main path(s)
            backup
                polling interval in seconds of back-up path(s)
            retry_delay
                delay in seconds before retrying an poll
            ok_msg
                map defining the message to be sent when a path recovers
            fail_msg
                map defining the message to be sent when a path fails
        """
        threading.Thread.__init__(self)
        self.account = account
        self.receiver = receiver
        self.line = line
        self.tpaths = paths
        self.tpaths_lock = pathlock
        self.parent = parent
        self.poll_retry_delay = retry_delay
        self.main_poll = None
        self.backup_poll = None
        self.routines = []
        
    def set_poll(self,  main,  backup,  ok_msg,  fail_msg):
        self.main_poll = main
        self.backup_poll = backup
        self.ok_msg = ok_msg
        self.fail_msg = fail_msg
        self.main_poll_next = 0
        self.backup_poll_next = 0
        self.main_poll_ok = 0
        self.backup_poll_ok = 0
        self.counter = 0
    
    def set_routines(self,  routines):
        self.routines = routines
        self.routine_nexts = []
        now = time.time()
        for routine in self.routines:
            if  'interval' in routine:
                interval = routine['interval']
            else:
                interval = 86400
            if 'start' in routine:
                start = (now % 86400 ) + routine['start']
            else:
                start = now
            while start < now:
                start += interval
            self.routine_nexts.append(start )

# -----------------
# send polls while needed (call in thread)
# at first run check all paths
# ------------------
    def run(self):
        first = 1
        while self.main_poll or self.backup_poll or len(self.routines) > 0:
            # on first poll check validity of all paths
            self.running = 1
            now = time.time()
            # ---------------
            # main poll 
            # ---------------
            main_polled = 0
            back_up_for_main = 0
            backup_polled = 0
            if self.main_poll != None and self.main_poll_next <= now:
                for ps in ('primary',  'secondary'):
                    if first or main_polled == 0:
                        if self.tpaths['main'][ps]['path'] != None:
                            if self.tpaths['main'][ps]['path'].poll():
                                main_polled = 1
                                self.counter += 1
                                if self.tpaths['main'][ps]['ok'] != 1:
                                    self.tpaths_lock.acquire()
                                    self.tpaths['main'][ps]['ok'] = 1
                                    self.tpaths_lock.release()
                                    self.msg(self.ok_msg, 1,  1)
                            else:
                                if self.tpaths['main'][ps]['ok'] != 0:
                                    self.tpaths_lock.acquire()
                                    self.tpaths['main'][ps]['ok'] = 0
                                    self.tpaths_lock.release()
                                    self.msg(self.fail_msg, 1,  0)
                            print('main' , ps)
                if main_polled == 0:
                    self.main_poll_ok = 0
                    self.main_ok = 0
                    back_up_for_main = 1
                else:
                    self.main_poll_ok = 1
                    self.main_ok = 1
                    self.main_poll_next = now + self.main_poll
            # ---------------
            # backup poll 
            # also triggered when main poll failed 
            # ---------------
            if self.backup_poll != None and (self.main_poll_next <= now or self.backup_poll_next <= now):
                for ps in ('primary',  'secondary'):
                    if first or backup_polled == 0:
                        if self.tpaths['back-up'][ps]['path'] != None:
                            if self.tpaths['back-up'][ps]['path'].poll():
                                backup_polled = 1
                                self.counter += 1
                                if self.tpaths['back-up'][ps]['ok'] != 1:
                                    self.tpaths_lock.acquire()
                                    self.tpaths['back-up'][ps]['ok'] = 1
                                    self.tpaths_lock.release()
                                    self.msg(self.ok_msg, 2,  1)
                            else:
                                if self.tpaths['back-up'][ps]['ok'] != 0:
                                    self.tpaths_lock.acquire()
                                    self.tpaths['back-up'][ps]['ok'] = 0
                                    self.tpaths_lock.release()
                                    self.msg(self.fail_msg, 2,  0)
                            print('back-up' , ps)
                if backup_polled == 0:
                    self.backup_poll_ok = 0
                    self.backup_ok = 0
                else:
                    self.backup_poll_ok = 1
                    self.backup_ok = 1
                    self.backup_poll_next = now + self.backup_poll
            if self.main_poll != None and main_polled and (self.backup_poll == None or backup_polled):
                first = 0
            # -----------------
            # schedule retry of main
            # -----------------
            if main_polled != 0 or (back_up_for_main and backup_polled):
                if self.main_poll != None and self.main_poll_next < now:
                        self.main_poll_next = now + self.main_poll
            # ------------------------------
            # handle routine messages
            # -----------------------------
            if len(self.routines) > 0:
                self.do_routines()
            # -------------------------
            # decide how long to sleep
            # -------------------------
            time.sleep(self.poll_retry_delay)
                
    def msg(self,  msg,  ps,  ok):
        """
        Send a message on poll state change
        """
        if msg != None:
            nmsg = msg
            nmsg['zone'] = ps
            if 'type' in msg:
                type = msg['type']
            else:
                if 'code' in msg:
                    code = msg['code']
                    if len(code) == 3:
                        type = 'ADM-CID'
                        if ok:
                            nmsg['q'] = 1
                        else:
                            nmsg['q'] = 3
                    elif len(code) == 2:
                        type = 'SIA-DCS'
            if type != None:
                self.parent.send_msg(type, msg)

    def stop(self):
        self.main_poll = None
        self.backup_poll = None
        self.routines = []
        
    def active(self):
        ret = 0
        if self.main_poll or self.backup_poll:
            ret += 1
        if len(self.routines) > 0:
            ret += 2
        return ret
    
    def count(self):
        return self.counter

    def do_routines(self):
        now = time.time()
        cnt = 0
        for n,  r in zip(self.routine_nexts,  self.routines):
            if n <= now:
                if 'type' in r:
                    type = r['type']
                else:
                    if 'code' in r:
                        code = r['code']
                        if len(code) == 3:
                            type = 'ADM-CID'
                        else:
                            type = 'SIA-DCS'
                    else:
                        type = 'SIA-DCS'
                self.parent.send_msg(type,  r)
                if  'interval' in r:
                    interval = r['interval']
                else:
                    interval = 86400
                self.routine_nexts[cnt] = now + interval
            cnt += 1            

class event_thread(threading.Thread):
    """
    Handle the transmitting of events of SPT (Secured Premises Transciever)
    """
    def __init__(self,  account, receiver,  line,  queue,  queuelock,  tpaths,  tpaths_lock):
        """
        Handle the Transmitting of events as defined in 
            SIA DC09 specification
            EN 50136-1
        
        parameters
            account
                Account number to be used. 
                Most receivers expect a numeric string of 4 to 8 digits
            receiver
                an optional integer to be used as receiver number in the block header
            line
                an optional integer to be used as line number in the block header
        """
        threading.Thread.__init__(self)
        self.account = account
        self.receiver = receiver
        self.line = line
        self.queue = queue
        self.queuelock = queuelock
        self.tpaths = tpaths
        self.tpaths_lock = tpaths_lock
        self.send_retry_delay = 0.5
# -----------------
# send events while needed (call in thread)
# checks message queue and retries
# ------------------
    def run(self):
        while  len(self.queue) > 0:
            # --------------------
            # first handle queue
            # --------------------
            self.running = 1
            sent = 1
            while sent and len(self.queue):
                sent = self.send()
#            now = time.time()
                # -------------------------
                # decide how long to sleep
                # -------------------------
    #            if self.main_poll_next < self.backup_poll_next:
    #                next = self.main_poll_next
    #            else:
    #                next = self.backup_poll_next
    #            if next > now:
                if len(self.queue):
                    time.sleep(self.send_retry_delay)
        self.running = 0
            
    def send(self):
        self.queuelock.acquire()
        if len(self.queue) == 0:
            self.queuelock.release()
            return
        mess = self.queue.popleft()
        self.queuelock.release()
        msg_sent = 0
        # ---------------------------
        # first try known good paths
        # --------------------------
        for mb in ('main',  'back-up'):
            for ps in ('primary',  'secondary'):
                if msg_sent == 0 and self.tpaths[mb][ps]['path'] != None:
                    if self.tpaths[mb][ps]['ok']:
                        if self.tpaths[mb][ps]['path'].message(mess[0], mess[1],  mess[2]):
                            msg_sent = 1
                            print(mb, ps)
        # ---------------------------
        # then try all available paths
        # --------------------------
        if msg_sent == 0:
            for mb in ('main',  'back-up'):
                for ps in ('primary',  'secondary'):
                    if msg_sent == 0 and self.tpaths[mb][ps]['path'] != None:
                        if self.tpaths[mb][ps]['path'].message(mess[0], mess[1],  mess[2]):
                            msg_sent = 1
                            self.tpaths_lock.acquire()
                            self.tpaths[mb][ps]['ok'] = 1
                            self.tpaths_lock.release()
                            print(mb, ps)
        if msg_sent == 0:
            self.queuelock.acquire()
            tup = mess[0], mess[1],  mess[2]
            self.queue.appendleft(tup)
            self.queuelock.release()
        return msg_sent
    
    def active(self):
        return self.running
            