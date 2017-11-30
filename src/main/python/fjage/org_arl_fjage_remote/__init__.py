import os as _os
import sys as _sys
import json as _json
import uuid as _uuid
import time as _time
import socket as _socket
import threading as _td
import logging as _log

from collections import OrderedDict
from org_arl_fjage import AgentID
from org_arl_fjage import Message
from org_arl_fjage import GenericMessage


def current_time_millis(): return int(round(_time.time() * 1000))


class Action:
    AGENTS = "agents"
    CONTAINS_AGENT = "containsAgent"
    SERVICES = "services"
    AGENT_FOR_SERVICE = "agentForService"
    AGENTS_FOR_SERVICE = "agentsForService"
    SEND = "send"
    SHUTDOWN = "shutdown"


class Gateway:
    """Gateway to communicate with agents from python.

    Supported JSON keys:
        id
        action
        inResponseTo
        agentID
        agentIDs
        service
        services
        answer
        message
        relay
    """

    DEFAULT_TIMEOUT = 1000
    NON_BLOCKING = 0
    BLOCKING = -1

    def __init__(self, hostname, port, name=None):
        """NOTE: Developer must make sure a duplicate name is not assigned to the Gateway."""

        # Mapping LogLevels between fjage.py and fjage
        # CRITICAL:50 - SEVERE (highest value)
        # ERROR:40
        # WARNING:30 WARNING
        # INFO:20 INFO
        # DEBUG:10 FINE/FINER/FINEST
        # NOTSET:0
        self.logger = _log.getLogger('org.arl.fjage')

        try:
            if name == None:
                self.name = "PythonGW-" + str(_uuid.uuid4())
            else:
                try:
                    self.name = name
                except Exception as e:
                    self.self.logger.critical("Exception: Cannot assign name to gateway: " + str(e))
                    raise

            self.q = list()
            self.subscribers = list()
            self.pending = dict()
            self.cv = _td.Condition()

            self.socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            self.recv_thread = _td.Thread(target=self.__recv_proc, args=(self.q, self.subscribers, ))
            self.recv_thread.daemon = True

            self.logger.info("Connecting to " + str(hostname) + ":" + str(port))
            self.socket.connect((hostname, port))
            self.socket_file = self.socket.makefile('r', 65536)

            self.recv_thread.start()

            if self._is_duplicate():
                self.logger.critical("Duplicate Gateway found. Shutting down.")
                self.socket.close
                raise Exception('DuplicateGatewayException')

        except Exception as e:
            self.logger.critical("Exception: " + str(e))
            raise

    def _parse_dispatch(self, rmsg, q):
        """Parse incoming messages and respond to them or dispatch them."""
        req = _json.loads(rmsg)
        rsp = dict()
        if "id" in req:
            req['id'] = _uuid.UUID(req['id'])

        if "action" in req:

            if req["action"] == Action.AGENTS:
                # self.logger.debug("ACTION: " + Action.AGENTS)

                rsp["inResponseTo"] = req["action"]
                rsp["id"] = str(req["id"])
                rsp["agentIDs"] = [self.name]
                self.socket.sendall((_json.dumps(rsp) + '\n').encode())

            elif req["action"] == Action.CONTAINS_AGENT:
                # self.logger.debug("ACTION: " + Action.CONTAINS_AGENT)
                rsp["inResponseTo"] = req["action"]
                rsp["id"] = str(req["id"])
                answer = False
                if req["agentID"]:
                    if req["agentID"] == self.name:
                        answer = True
                rsp["answer"] = answer
                self.socket.sendall((_json.dumps(rsp) + '\n').encode())

            elif req["action"] == Action.SERVICES:
                # self.logger.debug("ACTION: " + Action.SERVICES)

                rsp["inResponseTo"] = req["action"]
                rsp["id"] = str(req["id"])
                rsp["services"] = []
                self.socket.sendall((_json.dumps(rsp) + '\n').encode())

            elif req["action"] == Action.AGENT_FOR_SERVICE:
                # self.logger.debug("ACTION: " + Action.AGENT_FOR_SERVICE)

                rsp["inResponseTo"] = req["action"]
                rsp["id"] = str(req["id"])
                rsp["agentID"] = ""
                self.socket.sendall((_json.dumps(rsp) + '\n').encode())

            elif req["action"] == Action.AGENTS_FOR_SERVICE:
                # self.logger.debug("ACTION: " + Action.AGENTS_FOR_SERVICE)
                rsp["inResponseTo"] = req["action"]
                rsp["id"] = str(req["id"])
                rsp["agentIDs"] = []
                self.socket.sendall((_json.dumps(rsp) + '\n').encode())

            elif req["action"] == Action.SEND:
                # self.logger.debug("ACTION: " + Action.SEND)

                # add message to queue only if:
                # 1. if the recipient is same as gateway's name or
                # 2. the message is for a topic in the subscribers list
                try:
                    msg = req["message"]
                    if msg["data"]["recipient"] == self.name:
                        q.append(msg)
                        self.cv.acquire()
                        self.cv.notify()
                        self.cv.release()

                    if self._is_topic(msg["data"]["recipient"]):
                        if self.subscribers.count(msg["data"]["recipient"].replace("#", "")):
                            q.append(msg)
                            self.cv.acquire()
                            self.cv.notify()
                            self.cv.release()

                except Exception as e:
                    self.logger.critical("Exception: Error adding to queue - " + str(e))

            elif value == Action.SHUTDOWN:
                self.logger.debug("ACTION: " + Action.SHUTDOWN)
                return None

            else:
                self.logger.warning("Invalid message, discarding")
        else:
            if "id" in req:
                if req['id'] in self.pending:
                    tup = self.pending[req["id"]]
                    self.pending[req["id"]] = (tup[0], req)
                    tup[0].set()
        return True

    def __recv_proc(self, q, subscribers):
        """Receive process."""

        parenthesis_count = 0
        rmsg = ""
        name = self.socket.getpeername()

        while True:
            try:
                rmsg = self.socket_file.readline()
                if not rmsg:
                    self.logger.critical("Exception: Socket Closed")

                self.logger.debug(str(name[0]) + ":" + str(name[1]) + " <<< " + rmsg)
                # Parse and dispatch incoming messages
                self._parse_dispatch(rmsg, q)
            except:
                self.logger.critical("Exception: " + str(e))
                pass

    def __del__(self):
        try:
            self.socket.close
        except Exception as e:
            self.logger.critical("Exception: " + str(e))

    def shutdown(self):
        """Shutdown master container."""

        j_dict = dict()
        j_dict["action"] = Action.SHUTDOWN
        self.socket.sendall((_json.dumps(j_dict) + '\n').encode())

    def send(self, msg, relay=True):
        """Sends a message to the recipient indicated in the message. The recipient may be an agent or a topic."""

        if not msg.recipient:
            return False

        j_dict = dict()
        m_dict = OrderedDict()
        d_dict = dict()
        j_dict["action"] = Action.SEND
        j_dict["relay"] = relay
        msg.sender = self.name
        # m_dict = self._to_json(msg)
        d_dict = self._to_json(msg)
        # m_dict["msgType"] = "org.arl."+msg.__module__+"."+msg.__class__.__name__
        # m_dict["clazz"] = "org.arl." + msg.__module__ + "." + msg.__class__.__name__
        m_dict["clazz"] = msg.__module__.replace("_", ".") + "." + msg.__class__.__name__
        m_dict["data"] = d_dict
        j_dict["message"] = m_dict
        # check for GenericMessage class and add "map" separately
        if msg.__class__.__name__ == GenericMessage().__class__.__name__:
            j_dict["map"] = msg.map

        json_str = _json.dumps(j_dict)
        name = self.socket.getpeername()
        self.logger.debug(str(name[0]) + ":" + str(name[1]) + " >>> " + json_str)

        self.socket.sendall((json_str + '\n').encode())

        return True

    def _retrieveFromQueue(self, filter):
        rmsg = None
        try:
            if filter == None and len(self.q):
                rmsg = self.q.pop()

            # If filter is a Message, look for a Message in the
            # receive Queue which was inReplyto that message.
            elif isinstance(filter, Message):
                if filter.msgID:
                    for i in self.q:
                        if "inReplyTo" in i and filter.msgID == i["inReplyTo"]:
                            try:
                                rmsg = self.q.pop(self.q.index(i))
                            except Exception as e:
                                self.logger.critical("Error: Getting item from list - " + str(e))

            # If filter is a class, look for a Message of that class.
            elif type(filter) == type(Message):
                for i in self.q:
                    if i["clazz"].split(".")[-1] == filter.__name__:
                        try:
                            rmsg = self.q.pop(self.q.index(i))
                        except Exception as e:
                            self.logger.critical("Error: Getting item from list - " + str(e))

            # If filter is a lambda, look for a Message that on which the
            # lambda returns True.
            elif isinstance(filter, type(lambda: 0)):
                for i in self.q:
                    if filter(i):
                        try:
                            rmsg = self.q.pop(self.q.index(i))
                        except Exception as e:
                            self.logger.critical("Error: Getting item from list - " + str(e))

        except Exception as e:
            self.logger.critical("Error: Queue empty/timeout - " + str(e))

        return rmsg

    def receive(self, filter=None, timeout=0):
        """Returns a message received by the gateway and matching the given filter."""
        rmsg = self._retrieveFromQueue(filter)
        if (rmsg == None and timeout != self.NON_BLOCKING):
            deadline = current_time_millis() + timeout
            while (rmsg == None and (timeout == self.BLOCKING or current_time_millis() < deadline)):

                if timeout == self.BLOCKING:
                    self.cv.acquire()
                    self.cv.wait()
                    self.cv.release()
                elif timeout > 0:
                    self.cv.acquire()
                    t = deadline - current_time_millis()
                    self.cv.wait(t / 1000)
                    self.cv.release()

                rmsg = self._retrieveFromQueue(filter)

        if not rmsg:
            return None

        try:
            rsp = self._from_json(rmsg)
            found_map = False

            # add map if it is a Generic message
            if rsp.__class__.__name__ == GenericMessage().__class__.__name__:
                if "map" in rmsg["data"]:
                    map = _json.loads(str(rmsg["data"]["map"]))
                    rsp.__dict__.update(map)
                    found_map = True

                if not found_map:
                    self.logger.warning("No map field found in Generic Message")

        except Exception as e:
            self.logger.critical("Exception: Class loading failed - " + str(e))
            return None

        return rsp

    def request(self, msg, timeout=1000):
        """Return received response message, null if none available."""
        self.send(msg)
        return self.receive(msg, timeout)

    def topic(self, topic):
        """Returns an object representing the named topic."""
        if isinstance(topic, str):
            return AgentID(topic, True)

        elif isinstance(topic, AgentID):
            if topic.is_topic:
                return topic
            return AgentID(topic.name + "__ntf", True)

        else:
            return AgentID(topic.__class__.__name__ + "." + str(topic), True)

    def subscribe(self, topic):
        """Subscribes the gateway to receive all messages sent to the given topic."""
        if isinstance(topic, AgentID):
            if topic.is_topic == False:
                new_topic = AgentID(topic.name + "__ntf", True)
            else:
                new_topic = topic

            if len(self.subscribers) == 0:
                self.subscribers.append(new_topic.name)
            else:
                # check whether this topic is already subscribed to
                if new_topic.name in self.subscribers:
                    self.logger.critical("Error: Already subscribed to topic")
                    return
                self.subscribers.append(new_topic.name)
        else:
            self.logger.critical("Invalid AgentID")

    def unsubscribe(self, topic):
        """Unsubscribes the gateway from a given topic."""
        if isinstance(topic, AgentID):
            if topic.is_topic == False:
                new_topic = AgentID(topic.name + "__ntf", True)

            if len(self.subscribers) == 0:
                return False

            try:
                # self.subscribers.remove(new_topic.name)
                self.subscribers.remove(topic.name)
            except:
                self.logger.critical("Exception: No such topic subscribed: " + new_topic.name)

            return True
        else:
            self.logger.critical("Invalid AgentID")

    def agentForService(self, service, timeout=1000):
        """ Finds an agent that provides a named service. If multiple agents are registered
            to provide a given service, any of the agents' id may be returned.
        """
        req_id = _uuid.uuid4()
        j_dict = dict()
        j_dict["action"] = Action.AGENT_FOR_SERVICE
        j_dict["id"] = str(req_id)
        if isinstance(service, str):
            j_dict["service"] = service
        else:
            j_dict["service"] = service.__class__.__name__ + "." + str(service)
        self.socket.sendall((_json.dumps(j_dict) + '\n').encode())

        res_event = _td.Event()
        self.pending[req_id] = (res_event, None)
        ret = res_event.wait(timeout)
        if not ret:
            return None
        else:
            tup = self.pending.pop(req_id)
            return tup[1]["agentID"] if "agentID" in tup[1] else None

    def agentsForService(self, service, timeout=1000):
        """Finds all agents that provides a named service."""

        req_id = _uuid.uuid4()
        j_dict = dict()
        j_dict["action"] = Action.AGENTS_FOR_SERVICE
        j_dict["id"] = str(req_id)
        if isinstance(service, str):
            j_dict["service"] = service
        else:
            j_dict["service"] = service.__class__.__name__ + "." + str(service)
        self.socket.sendall((_json.dumps(j_dict) + '\n').encode())

        res_event = _td.Event()
        self.pending[req_id] = (res_event, None)
        ret = res_event.wait(timeout)
        if not ret:
            return None
        else:
            tup = self.pending.pop(req_id)
            return tup[1]["agentIDs"] if "agentIDs" in tup[1] else None

    def _to_json(self, inst):
        """Convert the object attributes to a dict."""
        dt = inst.__dict__.copy()

        for key in list(dt):
            if dt[key] == None:
                dt.pop(key)
            # if the last charactor of an attribute is "_", remove it in json message. E.g. from_
            elif list(key)[-1] == '_':
                dt[key[:-1]] = dt.pop(key)

            # remove map if its a GenericMessage class (to be added later)
            if key == 'map':
                dt.pop(key)

            # TODO: Any attribute ending with "_", remove it
        return dt

    def _from_json(self, dt):
        """If possible, do class loading, else return the dict."""

        # for testing various incoming message types
        # dt['msgType'] = 'org.arl.fjage.shell.ShellExecReq'
        # dt['msgType'] = 'org.arl.fjage.messages.GenericMessage'
        if 'clazz' in dt:
            class_name = dt['clazz'].split(".")[-1]
            module_name = dt['clazz'].split(".")
            module_name.remove(module_name[-1])
            module_name = "_".join(module_name)

            try:
                module = __import__(module_name)
            except Exception as e:
                self.logger.critical("Exception in from_json, module: " + str(e))
                return dt
            try:
                class_ = getattr(module, class_name)
            except Exception as e:
                self.logger.critical("Exception in from_json, class: " + str(e))
                return dt
            # args = dict((key.encode('ascii'), value.encode('ascii')) for key, value in dt.items())
            # args = dict((key.encode('ascii'), value if (isinstance(value, int) or isinstance(value, float)) else value.encode('ascii')) for key, value in dt.items())
            args = dict()
            for key, value in dt["data"].items():
                args[key] = value

            inst = class_(**args)
        else:
            inst = dt
        return inst

    def _is_duplicate(self):
        req_id = _uuid.uuid4()
        req = dict()
        req["action"] = Action.CONTAINS_AGENT
        req["id"] = str(req_id)
        req["agentID"] = self.name
        self.socket.sendall((_json.dumps(req) + '\n').encode())

        res_event = _td.Event()
        self.pending[req_id] = (res_event, None)
        ret = res_event.wait(self.DEFAULT_TIMEOUT)
        if not ret:
            return True
        else:
            tup = self.pending.pop(req_id)
            return tup[1]["answer"] if "answer" in tup[1] else True

    def _is_topic(self, recipient):
        if recipient[0] == "#":
            return True
        return False
