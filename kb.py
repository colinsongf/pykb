#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging; kblogger = logging.getLogger("kb");
DEBUG_LEVEL=logging.WARN

import sys
from errno import ECONNREFUSED
import threading, asyncore
import asynchat
import socket
import time

import shlex
import json

try:
    from Queue import Queue, Empty
except ImportError: #Python3 compat
    from queue import Queue, Empty


EVENT_POLLING_RATE = 20 #Hz
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


class EventCallbackExecutor(threading.Thread):

    def __init__(self, in_event_queue, out_polled_event_queue, callback_queue):
        threading.Thread.__init__(self)

        self.running = True
        self._events = in_event_queue
        self._polled_events = out_polled_event_queue
        self._callbacks_queue = callback_queue
        self._callbacks = {}

    def run(self):

        while self.running:
            # wait for an event. Can not be blocking else we can not join the thread.
            eventid, value = None, None
            try:
                eventid, value = self._events.get_nowait()
            except Empty:
                time.sleep(1. / EVENT_POLLING_RATE)
                continue

            # check if new callbacks have been registered
            try:
                while True:
                    newid, cb = self._callbacks_queue.get_nowait()
                    self._callbacks.setdefault(newid,[]).append(cb)
            except Empty:
                pass

            if eventid not in self._callbacks:
                # no callback associated. Put it back to the event queue for manual
                # polling by the user
                self._polled_events.put((eventid, value))
                self._events.task_done()
            else:
                for cb in self._callbacks[eventid]:
                    kblogger.debug("Executing callback %s" % cb.__name__)
                    cb(value)
                    self._events.task_done()

    def close(self):
        # make sure all received events are handled
        self._events.join()
        self.running = False
        self.join()


MSG_SEPARATOR = "#end#"

KB_OK="ok"
KB_ERROR="error"
KB_EVENT="event"

class KB:

    def __init__(self, host='localhost', port=DEFAULT_PORT, sock=None):

        self._asyncore_thread = threading.Thread( target = asyncore.loop, kwargs = {'timeout': .1} )
        

        #incoming events
        self._internal_events = Queue()
        # events that are not dealt with a callback
        self.events = Queue()
        self._callbackexecutor = None

        self._client = KBClient(self._internal_events, host, port, sock)
        self._asyncore_thread.start()

        #add to the KB class all the methods the server declares
        methods = self._client.call_server("methods")
        for m in methods:
            self.add_method(m)

        #new subscribers. The callbackExecutor thread is started only at the
        # end of the constructor -> we first want to be sure we were able
        # to connect to the knowledge base
        self._registered_callbacks = Queue()
        self._callbackexecutor = EventCallbackExecutor(self._internal_events, self.events, self._registered_callbacks)
        self._callbackexecutor.start()


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
        if self._callbackexecutor:
            self._callbackexecutor.close()
        self._client.close_when_done()
        self._asyncore_thread.join()

    def subscribe(self, pattern, callback = None, var = None, type = 'NEW_INSTANCE', trigger = 'ON_TRUE', agent = 'default'):
        """ Allows to subscribe to an event, and get notified when the event is 
        triggered.

        Example with callbacks:
        >>> def onevent(evt):
        >>>     print("In callback. Got evt %s" % evt)
        >>>
        >>> self.kb.subscribe(["?o isIn room"], onevent)
        >>> self.kb += ["alfred isIn room"]
        >>> # 'onevent' get called
        In callback. Got evt [u'alfred']

        Example with 'polled' events:
        >>> evt_id = self.kb.subscribe(["?o isIn room"])
        >>> self.kb += ["alfred isIn room"]
        >>> print(str(self.kb.events.get()))
        ('evt_7694742461071211105', [u'alfred'])

        If 'callback' is provided, the callback will be invoked with the result of 
        the event (content depend on event type) *in a separate thread*.
        If not callback is provided, the incoming events are stored in the 
        KB.events queue, and you can poll them yourself (which allow for better 
        control of the execution flow).

        The 'var' parameter can be used with the 'NEW_INSTANCE' type of event to
        tell which variable must be returned.

        The 'model' parameter allows for registering an event in a specific model.
        By default, the pattern is monitored on every models.

        Returns the event id of the newly created event.
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

        event_id = self.server_subscribe(type, trigger, var, pattern, agent)
        kblogger.debug("New event successfully registered with ID " + event_id)
        if callback:
            self._registered_callbacks.put((event_id, callback))

        return event_id

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

    use_encoding = 0 # Python2 compat.

    def __init__(self, event_queue, host='localhost', port=DEFAULT_PORT, sock=None):
        asynchat.async_chat.__init__(self, sock=sock)
        if not sock:
            self.create_socket(family=socket.AF_INET, type=socket.SOCK_STREAM)
            self.connect( (host, port) )

        self.host = host
        self.port = port

        self.set_terminator(MSG_SEPARATOR)
        self._in_buffer = b""
        self._incoming_response = Queue()

        self._events = event_queue

    def collect_incoming_data(self, data):
        self._in_buffer = self._in_buffer + data

    def found_terminator(self):
        status, value = self.decode(self._in_buffer)

        if status == KB_EVENT:
            kblogger.debug("Event received: %s (%s)" % value)
            self._events.put(value)
        else:
            self._incoming_response.put((status, value))

        self._in_buffer = b""

    def handle_error(self):
        exctype, value = sys.exc_info()[:2]

        if exctype == socket.error and value.errno == ECONNREFUSED:
            kblogger.error("Connection refused!")
            self.handle_close()
            return
        
        kblogger.error("Unhandled exception: %s: %s" % (exctype, value))
        raise exctype(value)

    def call_server(self, method, *args):
        self.push(self.encode(method, *args))

        status, value = None, None
        while True:
            try:
                status, value = self._incoming_response.get(True, 0.5) # leaves 500ms to connect
                break
            except Empty:
                if not self.connected:
                    raise KbError("Can not connect to the knowledge base on %s:%s" % (self.host, self.port))

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
            return "event", (parts[1], json.loads(parts[2]))
        elif parts[0] == "error":
            return "error", "%s: %s"%(parts[1], parts[2])
        else:
            raise KbError("Got an unexpected message status from the knowledge base: %s"%parts[0])

    #### patch code from asynchat, ``del deque[0]`` is not safe #####
    def initiate_send(self):
        while self.producer_fifo and self.connected:
            first = self.producer_fifo.popleft()
            # handle empty string/buffer or None entry
            if not first:
                if first is None:
                    self.handle_close()
                    return

            # handle classic producer behavior
            obs = self.ac_out_buffer_size
            try:
                data = first[:obs]
            except TypeError:
                data = first.more()
                if data:
                    self.producer_fifo.appendleft(data)
                continue

            if isinstance(data, str) and self.use_encoding:
                data = bytes(data, self.encoding)

            # send the data
            try:
                num_sent = self.send(data)
            except socket.error:
                self.handle_error()
                return

            if num_sent:
                if num_sent < len(data) or obs < len(first):
                    self.producer_fifo.appendleft(first[num_sent:])
            # we tried to send some actual data
            return


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
