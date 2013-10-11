#!/usr/bin/python
"""Python bindings for KB-API conformant knowledge bases.

It also supports an extension for events.

This library use the standard Python logging mechanism.
You can retrieve kb log messages through the "kb" logger. See the end of
this file for an example showing how to display to the console the log messages.
"""
import time
import logging
import socket
import select
from threading import Thread
import ast
import shlex

try:
    from Queue import Queue
except ImportError: #Python3 compat
    from queue import Queue

DEBUG_LEVEL=logging.INFO


class NullHandler(logging.Handler):
    """Defines a NullHandler for logging, in case kb is used in an application
    that doesn't use logging.
    """
    def emit(self, record):
        pass

kblogger = logging.getLogger("kb")

h = NullHandler()
kblogger.addHandler(h)

class KbServerError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

class KB(Thread):
    def __init__(self, host = "localhost", port = 6969):
        Thread.__init__(self)
        
        self.port = port
        self.host = host

        self._kb_requests_queue = Queue()
        self._kb_responses_queue = Queue()
        
        self._running = True
        
        self._kb_server = None
        
        #This map stores the ids of currently registered events and the corresponding
        #callback function.
        self._registered_events = {}
        
        try:
            #create an INET, STREAMing socket
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            #now connect to the kb server
            self.s.connect((host, port))
            self._kb_server = self.s.makefile(mode='rw')
        except socket.error:
            self.s.close()
            raise KbServerError('Unable to connect to the server. Check it is running and ' + \
                                 'that you provided the right host and port.')
        
        kblogger.debug("Socket connection established")
        self.start()
        #get the list of methods currenlty implemented by the server
        try:
            res = self.call_server(["api"])
            self.rpc_methods = [(t.split('(')[0], len(t.split(','))) for t in res]
        except KbServerError:
            self._kb_server.close()
            self.s.close()
            raise KbServerError('Cannot initialize the kb connector! Smthg wrong with the server!')
        
        #add the the KB class all the methods the server declares
        for m in self.rpc_methods:
            self.add_methods(m)
    
    def run(self):
        """ This method reads and writes to/from the ontology server.
        When a new request is pushed in _kb_requests_queue, it is send to the 
        server, when the server answer smthg, we check if it's a normal answer
        or an event, and dispatch accordingly the answer's content.
        """
        
        inputs = [self._kb_server]
        outputs = [self._kb_server]
        
        while self._running:
        
            try:
                inputready,outputready,exceptready = select.select(inputs, outputs, [])
            except select.error as e:
                break
            except socket.error as e:
                break
            
            if not self._kb_requests_queue.empty():
                for o in outputready:
                    if o == self._kb_server:
                        for r in self._kb_requests_queue.get():
                            self._kb_server.write(r)
                            self._kb_server.write("\n")
                            
                        self._kb_server.write("#end#\n")
                        self._kb_server.flush()
            
            for i in inputready:
                if i == self._kb_server:
                    # TODO: issue here if we have more than one message
                    # queued. The second one is discarded. This cause
                    # for instance some event to be shallowed...
                    res = self.get_kb_response()
                    
                    if res['status'] == "event": #notify the event
                        try:
                            evt_id = res['value'][0]
                            evt_params = res['value'][1:]
                            
                            cbThread = Thread(target=self._registered_events[evt_id], args=evt_params)
                            cbThread.start()
                            kblogger.debug("Event notified")
                            
                        except KeyError:
                            kblogger.error("Got a event notification, but I " + \
                            "don't know event " + evt_id)
                    else: #it's probably the answer to a request, push it forward.
                        self._kb_responses_queue.put(res)
            
            time.sleep(0.05)
    
    
    def subscribe(self, pattern, callback, var = None, type = 'NEW_INSTANCE', trigger = 'ON_TRUE', agent = 'myself'):
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
        
        event_args = [agent, type, trigger, var, pattern] if var else [agent, type, trigger, pattern]
        try:
            event_id = self.registerEventForAgent(*event_args)
            kblogger.debug("New event successfully registered with ID " + event_id)
            self._registered_events[event_id] = callback
        except AttributeError:
            kblogger.error("The server seems not to support events! check the server" + \
            " version & configuration!")
    
    def get_kb_response(self):
        kb_answer = {'status': self._kb_server.readline().rstrip('\n'), 'value':[]}
        
        while True:
            next = self._kb_server.readline().rstrip('\n')
            
            if next == "#end#":
                break
            
            if next == '':
                continue
                
            #special case for boolean that can not be directly evaluated by Python
            #since the server return true/false in lower case
            elif next.lower() == 'true':
                res = True
            elif next.lower() == 'false':
                res = False
            
            else:
                try:
                    res = ast.literal_eval(next)
                except SyntaxError:
                    res = next
                except ValueError:
                    res = next
            
            kb_answer['value'].append(res)
            
        kblogger.debug("Got answer: " + kb_answer['status'] + ", " + str(kb_answer['value']))
        
        return kb_answer
    
    
    def call_server(self, req):
        
        self._kb_requests_queue.put(req)
        
        res = self._kb_responses_queue.get() #block until we get an answer
        
        if res['status'] == 'ok':
            if not res['value']:
                return None
            return res['value'][0]
            
        elif res['status'] == 'error':
            msg = ": ".join(res['value'])
            raise KbServerError(msg)
        
        else:
            raise KbServerError("Got an unexpected message status from the knowledge base: " + \
            res['status'])
        
    def add_methods(self, m):
        def innermethod(*args):
            req = ["%s" % m[0]]
            for a in args:
                req.append(str(a))
            kblogger.debug("Sending request: " + req[0])
            return self.call_server(req)
                
        innermethod.__doc__ = "This method is a proxy for the knowledge server %s method." % m[0]
        innermethod.__name__ = m[0]
        setattr(self,innermethod.__name__,innermethod)
    
    def close(self):
        self._running = False
        self.join()
        kblogger.info('Closing the connection to the knowledge server...')
        self._kb_server.close()
        self.s.close()
        kblogger.debug('Done. Bye bye!')
    
    def __del__(self):
        if self._kb_server:
            self.close()
            
    def __getitem__(self, pattern, agent='myself'):
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
            return self.findForAgent(agent, "?_var", [stmt.replace("*", "?_var") for stmt in pattern])
        
        else:
            if "*" in pattern:
                return self.findForAgent(agent, "?_var", [pattern.replace("*", "?_var")])
            else:
                lookup = self.lookupForAgent(agent, pattern)
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
        """ This method allows to easily remove statements from the ontology
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
        
        self.remove(stmts)
        
        return self

if __name__ == '__main__':

    console = logging.StreamHandler()
    DEBUG_LEVEL=logging.DEBUG
    kblogger.setLevel(DEBUG_LEVEL)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(asctime)-15s %(name)s: %(levelname)s - %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    kblogger.addHandler(console)
    
    HOST = 'localhost'    # kb-server host
    PORT = 6969        # kb-server port
    
    kb = KB(HOST, PORT)
    
    def printer(c):
        print("Yeahh! event content: " + str(c))
    
    kblogger.info("Starting now...")
    try:
        kb.lookup("PurposefulAction")
        #kb.subscribe(["?o isIn room"], printer)
        
        #kb.processNL("learn that today is sunny")
        kb += ["johnny rdf:type Human", "johnny rdfs:label \"A que Johnny\""]
        kb += ["alfred rdf:type Human", "alfred likes icecream"]
        
        for human in kb["* rdf:type Human"]:
            print(human)
        
        for icecream_lovers in kb[["* rdf:type Human", "* likes icecream"]]:
            print(human)
            
        print(kb["A que Johnny"])
        
        if 'johnny' in kb:
            print("Johnny is here!")
        
        if not 'tartempion' in kb:
            print('No tartempion :-(')
        
        if 'alfred likes icecream' in kb:
            print("Alfred do like icecreams!")
        
        if 'alfred likes judo' in kb:
            print("Alfred do like judo!")
        
        kb -= "alfred rdf:type Human"
        
        for human in kb["* rdf:type Human"]:
            print(human)
            
        #if kb.check("[johnny rdf:type Human, johnny rdfs:label \"A que Johnny\"]"):
        #    print "Yeaaaah"
        
        
        #kb.addForAgent("johnny", "[hrp2 rdf:type Robot]")
        #print(kb.lookup("A que Johnny")[0])
        
        #for r in kb.find("bottle", "[?bottle rdf:type Bottle]"):
        #    print r

        
        #kb.add(["tutuo isIn room"])
        
        #time.sleep(1)
        
        kblogger.info("done!")
        
        
    except KbServerError as ose:
        print('Oups! An error occured!')
        print(ose)
    finally:
        kb.close()
