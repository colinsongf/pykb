#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging; kblogger = logging.getLogger("kb");
DEBUG_LEVEL=logging.WARN

import sys
import threading, asyncore
import asynchat
import socket

import shlex
import json

try:
    from Queue import Queue
except ImportError: #Python3 compat
    from queue import Queue


DEFAULT_PORT = 6969

class NullHandler(logging.Handler):
    """Defines a NullHandler for logging, in case kb is used in an application
    that doesn't use logging.
    """
    def emit(self, record):
        pass

kblogger = logging.getLogger("kb")

h = NullHandler()
kblogger.addHandler(h)

class KbError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


MSG_SEPARATOR = "#end#"

KB_OK="ok"
KB_ERROR="error"
KB_EVENT="event"

class KB:

    def __init__(self, host='localhost', port=DEFAULT_PORT, sock=None):

        self._asyncore_thread = threading.Thread( target = asyncore.loop, kwargs = {'timeout': .1} )
        

        self._client = KBClient(host, port, sock)
        self._asyncore_thread.start()

        #add to the KB class all the methods the server declares
        methods = self._client.call_server("methods")
        for m in methods:
            self.add_method(m)

        #callback function.
        self._registered_events = {}
 
    def add_method(self, m):
        m = str(m) # convert from unicode...
        def innermethod(*args):
            kblogger.debug("Sending <%s> request to server."%m)
            return self._client.call_server(m, *args)
                
        innermethod.__doc__ = "This method is a proxy for the knowledge server %s method." % m
        #HACK: special case for the server's subscribe method: we want to override it
        # to provide proper Python callback.
        innermethod.__name__ = m if m != "subscribe" else "server_subscribe"
        setattr(self,innermethod.__name__,innermethod)

    def __del__(self):
        self.close()

    def close(self):
        self._client.close_when_done()
        self._asyncore_thread.join()

    def subscribe(self, pattern, callback, var = None, type = 'NEW_INSTANCE', trigger = 'ON_TRUE', agent = 'default'):
        """ Allows to subscribe to an event, and get notified when the event is 
        triggered. This replace kb's registerEvent. Do not call KB.registerEvent()
        directly since it doesn't allow to define a callback function.
        
        The 'var' parameter can be used with the 'NEW_INSTANCE' type of event to
        tell which variable must be returned.

        The 'agent' parameter allows for registering an event in a specific model. By default,
        the main (robot) model is used.
        """
        
        if isinstance(pattern, basestring):
            pattern = [pattern]
        
        if type == 'NEW_INSTANCE' and not var:
            #Look if there's more than one variable in the pattern
            vars = set()
            for ps in pattern:
                vars |= set([s for s in shlex.split(ps) if s[0] == '?'])
            if len(vars) > 1:
                raise AttributeError("You must specify which variable must be returned " + \
                "when the event is triggered by setting the 'var' parameter")
            if len(vars) == 1:
                var = vars.pop()
        
        try:
            event_id = self.server_subscribe(type, trigger, var, pattern, agent)
            kblogger.debug("New event successfully registered with ID " + event_id)
            self._registered_events[event_id] = callback
        except AttributeError:
            kblogger.error("The server seems not to support events! check the server" + \
            " version & configuration!")
 
    def __getitem__(self, pattern, agent='default'):
        """This method introduces a different way of querying the ontology server.
        It uses the args (be it a string or a set of strings) to find concepts
        that match the pattern.
        An optional 'agent' parameter can be given to specify in which model the 
        query is executed.
        
        Differences with a simple 'find':
         - it uses '*' instead of '?varname' (but unbound variable starting
         with a '?' are still valid to describe relations between concepts)
         - it can be use to do a lookup
        
        Use example:
        kb = KB(<host>, <port>)
        
        for agent in kb["* rdf:type Agent"]
            ...
        
        if kb[["* livesIn ?house", "?house isIn toulouse"], agent='GERALD']
            ...
        
        #Assuming 'toulouse' has label "ville rose":
        city_id = kb["ville rose"]
        """
        
        if type(pattern) == list:
            return self.find(["?_var"], [stmt.replace("*", "?_var") for stmt in pattern], None, agent)
        
        else:
            if "*" in pattern:
                return self.find(["?_var"], [pattern.replace("*", "?_var")], None, agent)
            else:
                lookup = self.lookup(pattern, agent)
                return [concept[0] for concept in lookup]
    
    def __contains__(self, pattern):
        """ This will return 'True' is either a concept - described by its ID or
        label- or a statement or a set of statement is present (or can be infered)
        in the ontology.
        
        This allows syntax like:
            if 'Toto' in kb:
                ...
            if 'toto sees tata' in kb:
                ...
        """
        if not (type(pattern) == list):
            #First, attempt a lookup
            if self.lookup(pattern):
                return True
            #Lookup didn't answer anything. Check if pattern it can be statement
            if len(shlex.split(pattern)) != 3:
                return False

            pattern = [pattern]
        
        return self.exist(pattern)
    
    def __iadd__(self, stmts):
        """ This method allows to easily add new statements to the ontology
        with the '+=' operator.
        It can only add statement to the robot's model (other agents' model are 
        not accessible).
        
        kb = KB(<host>, <port>)
        kb += "toto likes icecream"
        kb += ["toto loves tata", "tata rdf:type Robot"]
        """
        if not (type(stmts) == list):
            stmts = [stmts]
        
        self.add(stmts)
        
        return self

    def __isub__(self, stmts):
        """ This method allows to easily retract statements from the ontology
        with the '-=' operator.
        It can only add statement to the robot's model (other agents' model are 
        not accessible).
        If a statement doesn't exist, it is silently skipped.
        
        kb = KB(<host>, <port>)
        kb -= "toto likes icecream"
        kb -= ["toto loves tata", "tata rdf:type Robot"]
        """
        if not (type(stmts) == list):
            stmts = [stmts]
        
        self.retract(stmts)
        
        return self



class KBClient(asynchat.async_chat):

    def __init__(self, host='localhost', port=DEFAULT_PORT, sock=None):
        asynchat.async_chat.__init__(self, sock=sock)
        if not sock:
            self.create_socket(family=socket.AF_INET, type=socket.SOCK_STREAM)
            self.connect( (host, port) )

        self.set_terminator(MSG_SEPARATOR)
        self._in_buffer = b""
        self._incoming_response = Queue()

    def collect_incoming_data(self, data):
        self._in_buffer = self._in_buffer + data

    def found_terminator(self):
        status, value = self.decode(self._in_buffer)
        self._incoming_response.put((status, value))
        self._in_buffer = b""

    def call_server(self, method, *args):
        self.push(self.encode(method, *args))
        status, value = self._incoming_response.get()
        if status == KB_ERROR:
            raise KbError(value)
        else:
            return value

    def encode(self, method, *args):
        return "\n".join([method] + [json.dumps(a) for a in args] + [MSG_SEPARATOR])

    def decode(self, raw):
        parts = raw.strip().split('\n')

        if parts[0] == "ok":
            if len(parts) > 1:
                return "ok", json.loads(parts[1])
            else:
                return "ok", None
        elif parts[0] == "event":
            return "event", parts[1]
        elif parts[0] == "error":
            return "error", "%s: %s"%(parts[1], parts[2])
        else:
            raise KbError("Got an unexpected message status from the knowledge base: %s"%parts[0])

    #def handle_error(self):
    #    print("Bonjour!")


if __name__ == '__main__':

    import time
    from logging import StreamHandler

    console = StreamHandler()
    kblogger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)-15s: %(message)s')
    console.setFormatter(formatter)
    kblogger.addHandler(console)


    kb = KB()


    time.sleep(.1)
    print("Closing now...")
    kb.close()
