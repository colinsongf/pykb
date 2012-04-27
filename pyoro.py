#!/usr/bin/python
"""A Python bridge to the ORO-server ontology server.

This library use the standard Python logging mechanism.
You can retrieve pyoro log messages through the "pyoro" logger. See the end of
this file for an example showing how to display to the console the log messages.
"""
import time
import logging
import socket
import select
from threading import Thread

try:
    from Queue import Queue
except ImportError: #Python3 compat
    from queue import Queue

DEBUG_LEVEL=logging.INFO


class NullHandler(logging.Handler):
    """Defines a NullHandler for logging, in case pyoro is used in an application
    that doesn't use logging.
    """
    def emit(self, record):
        pass

pyorologger = logging.getLogger("pyoro")

h = NullHandler()
pyorologger.addHandler(h)

class OroServerError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)

class Oro(Thread):
    def __init__(self, host = "localhost", port = 6969):
        Thread.__init__(self)
        
        self.port = port
        self.host = host

        self._oro_requests_queue = Queue()
        self._oro_responses_queue = Queue()
        
        self._running = True
        
        self._oro_server = None
        
        #This map stores the ids of currently registered events and the corresponding
        #callback function.
        self._registered_events = {}
        
        try:
            #create an INET, STREAMing socket
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            #now connect to the oro server
            self.s.connect((host, port))
            self._oro_server = self.s.makefile(mode='rw')
        except socket.error:
            self.s.close()
            raise OroServerError('Unable to connect to the server. Check it is running and ' + \
                                 'that you provided the right host and port.')
        
        self.start()
        #get the list of methods currenlty implemented by the server
        try:
            res = self.call_server(["listSimpleMethods"])
            self.rpc_methods = [(t.split('(')[0], len(t.split(','))) for t in res]
        except OroServerError:
            self._oro_server.close()
            self.s.close()
            raise OroServerError('Cannot initialize the oro connector! Smthg wrong with the server!')
        
        #add the the Oro class all the methods the server declares
        for m in self.rpc_methods:
            self.add_methods(m)
    
    def run(self):
        """ This method reads and writes to/from the ontology server.
        When a new request is pushed in _oro_requests_queue, it is send to the 
        server, when the server answer smthg, we check if it's a normal answer
        or an event, and dispatch accordingly the answer's content.
        """
        
        inputs = [self._oro_server]
        outputs = [self._oro_server]
        
        while self._running:
        
            try:
                inputready,outputready,exceptready = select.select(inputs, outputs, [])
            except select.error as e:
                break
            except socket.error as e:
                break
            
            if not self._oro_requests_queue.empty():
                for o in outputready:
                    if o == self._oro_server:
                        for r in self._oro_requests_queue.get():
                            self._oro_server.write(r)
                            self._oro_server.write("\n")
                            
                        self._oro_server.write("#end#\n")
                        self._oro_server.flush()
            
            for i in inputready:
                if i == self._oro_server:
                    # TODO: issue here if we have more than one message
                    # queued. The second one is discarded. This cause
                    # for instance some event to be shallowed...
                    res = self.get_oro_response()
                    
                    if res['status'] == "event": #notify the event
                        try:
                            evt_id = res['value'][0]
                            evt_params = res['value'][1:]
                            
                            cbThread = Thread(target=self._registered_events[evt_id], args=evt_params)
                            cbThread.start()
                            pyorologger.debug("Event notified")
                            
                        except KeyError:
                            pyorologger.error("Got a event notification, but I " + \
                            "don't know event " + evt_id)
                    else: #it's probably the answer to a request, push it forward.
                        self._oro_responses_queue.put(res)
            
            time.sleep(0.05)
    
    
    def subscribe(self, pattern, callback, var = None, type = 'NEW_INSTANCE', trigger = 'ON_TRUE', agent = 'myself'):
        """ Allows to subscribe to an event, and get notified when the event is 
        triggered. This replace ORO's registerEvent. Do not call Oro.registerEvent()
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
                vars |= set([s for s in ps.split() if s[0] == '?'])
            if len(vars) > 1:
                raise AttributeError("You must specify which variable must be returned " + \
                "when the event is triggered by setting the 'var' parameter")
            if len(vars) == 1:
                var = vars.pop()
        
        event_args = [agent, type, trigger, var, pattern] if var else [agent, type, trigger, pattern]
        try:
            event_id = self.registerEventForAgent(*event_args)
            pyorologger.debug("New event successfully registered with ID " + event_id)
            self._registered_events[event_id] = callback
        except AttributeError:
            pyorologger.error("The server seems not to support events! check the server" + \
            " version & configuration!")
    
    def get_oro_response(self):
        oro_answer = {'status': self._oro_server.readline().rstrip('\n'), 'value':[]}
        
        while True:
            next = self._oro_server.readline().rstrip('\n')
            
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
                    res = eval(next)
                except SyntaxError:
                    res = next
                except NameError:
                    res = next
            
            oro_answer['value'].append(res)
            
        pyorologger.debug("Got answer: " + oro_answer['status'] + ", " + str(oro_answer['value']))
        
        return oro_answer
    
    
    def call_server(self, req):
        
        self._oro_requests_queue.put(req)
        
        res = self._oro_responses_queue.get() #block until we get an answer
        
        if res['status'] == 'ok':
            if not res['value']:
                return None
            return res['value'][0]
            
        elif res['status'] == 'error':
            msg = ": ".join(res['value'])
            raise OroServerError(msg)
        
        else:
            raise OroServerError("Got an unexpected message status from ORO: " + \
            res['status'])
        
    def add_methods(self, m):
        def innermethod(*args):
            req = ["%s" % m[0]]
            for a in args:
                req.append(str(a))
            pyorologger.debug("Sending request: " + req[0])
            return self.call_server(req)
                
        innermethod.__doc__ = "This method is a proxy for the oro-server %s method." % m[0]
        innermethod.__name__ = m[0]
        setattr(self,innermethod.__name__,innermethod)
    
    def close(self):
        self._running = False
        self.join()
        pyorologger.info('Closing the connection to ORO...')
        self._oro_server.close()
        self.s.close()
        pyorologger.debug('Done. Bye bye!')
    
    def __del__(self):
        if self._oro_server:
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
        oro = Oro(<host>, <port>)
        
        for agent in oro["* rdf:type Agent"]
            ...
        
        if oro[["* livesIn ?house", "?house isIn toulouse"], agent='GERALD']
            ...
        
        #Assuming 'toulouse' has label "ville rose":
        city_id = oro["ville rose"]
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
            if 'Toto' in oro:
                ...
            if 'toto sees tata' in oro:
                ...
        """
        if not (type(pattern) == list):
            #First, attempt a lookup
            if self.lookup(pattern):
                return True
            #Lookup didn't answer anything. Check if pattern it can be statement
            if len(pattern.split()) != 3:
                return False
            else:
                pattern = [pattern]
        
        try:
            return self.check(pattern)
        except OroServerError:
            return False
    
    def __iadd__(self, stmts):
        """ This method allows to easily add new statements to the ontology
        with the '+=' operator.
        It can only add statement to the robot's model (other agents' model are 
        not accessible).
        
        oro = Oro(<host>, <port>)
        oro += "toto likes icecream"
        oro += ["toto loves tata", "tata rdf:type Robot"]
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
        
        oro = Oro(<host>, <port>)
        oro -= "toto likes icecream"
        oro -= ["toto loves tata", "tata rdf:type Robot"]
        """
        if not (type(stmts) == list):
            stmts = [stmts]
        
        self.remove(stmts)
        
        return self

if __name__ == '__main__':

    console = logging.StreamHandler()
    pyorologger.setLevel(DEBUG_LEVEL)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(asctime)-15s %(name)s: %(levelname)s - %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    pyorologger.addHandler(console)
    
    HOST = 'localhost'    # ORO-server host
    PORT = 6969        # ORO-server port
    
    oro = Oro(HOST, PORT)
    
    def printer(c):
        print("Yeahh! event content: " + str(c))
    
    pyorologger.info("Starting now...")
    try:
        oro.lookup("PurposefulAction")
        #oro.subscribe(["?o isIn room"], printer)
        
        #oro.processNL("learn that today is sunny")
        oro += ["johnny rdf:type Human", "johnny rdfs:label \"A que Johnny\""]
        oro += ["alfred rdf:type Human", "alfred likes icecream"]
        
        for human in oro["* rdf:type Human"]:
            print(human)
        
        for icecream_lovers in oro[["* rdf:type Human", "* likes icecream"]]:
            print(human)
            
        print(oro["A que Johnny"])
        
        if 'johnny' in oro:
            print("Johnny is here!")
        
        if not 'tartempion' in oro:
            print('No tartempion :-(')
        
        if 'alfred likes icecream' in oro:
            print("Alfred do like icecreams!")
        
        if 'alfred likes judo' in oro:
            print("Alfred do like judo!")
        
        oro -= "alfred rdf:type Human"
        
        for human in oro["* rdf:type Human"]:
            print(human)
            
        #if oro.check("[johnny rdf:type Human, johnny rdfs:label \"A que Johnny\"]"):
        #    print "Yeaaaah"
        
        
        #oro.addForAgent("johnny", "[hrp2 rdf:type Robot]")
        #print(oro.lookup("A que Johnny")[0])
        
        #for r in oro.find("bottle", "[?bottle rdf:type Bottle]"):
        #    print r

        
        #oro.add(["tutuo isIn room"])
        
        #time.sleep(1)
        
        pyorologger.info("done!")
        
        
    except OroServerError as ose:
        print('Oups! An error occured!')
        print(ose)
    finally:
        oro.close()
