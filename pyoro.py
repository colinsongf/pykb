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
from Queue import Queue

DEBUG_LEVEL=logging.DEBUG


class NullHandler(logging.Handler):
    """Defines a NullHandler for logging, in case pyoro is used in an application
    that doesn't use logging.
    """
    def emit(self, record):
        pass

logger = logging.getLogger("pyoro")

h = NullHandler()
logger.addHandler(h)

class OroServerError(Exception):
	def __init__(self, value):
		self.value = value
	def __str__(self):
		return repr(self.value)

class Oro(Thread):
	def __init__(self, host, port):
		Thread.__init__(self)
		
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
			self._oro_server = self.s.makefile()
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
					res = self.get_oro_response()
					
					if res['status'] == "event": #notify the event
						try:
							evt_id = res['value'][0]
							evt_params = res['value'][1:]
							
							self._registered_events[evt_id](*evt_params)
							logging.log(4, "Event notified")
							
						except KeyError:
							logger.error("Got a event notification, but I " + \
							"don't know event " + evt_id)
					else: #it's probably the answer to a request, push it forward.
						self._oro_responses_queue.put(res)
			
			time.sleep(0.05)
	
	
	def subscribe(self, pattern, callback, var = None, type = 'NEW_INSTANCE', trigger = 'ON_TRUE'):
		""" Allows to subscribe to an event, and get notified when the event is 
		triggered. This replace ORO's registerEvent. Do not call Oro.registerEvent()
		directly since it doesn't allow to define a callback function.
		
		The 'var' parameter can be used with the 'NEW_INSTANCE' type of event to
		tell which variable must be returned.
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
		
		event_args = [type, trigger, var, pattern] if var else [type, trigger, pattern]
		
		try:
			event_id = self.registerEvent(*event_args)
			logger.log(4, "New event successfully registered with ID " + event_id)
			self._registered_events[event_id] = callback
		except AttributeError:
			logger.error("The server seems not to support events! check the server" + \
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
			
		logger.log(4, "Got answer: " + oro_answer['status'] + ", " + str(oro_answer['value']))
		
		return oro_answer
	
	
	def call_server(self, req):
		
		self._oro_requests_queue.put(req)
		
		res = self._oro_responses_queue.get() #block until we get an answer
		
		if res['status'] == 'ok':
			if not res['value']:
				return None
			return res['value'][0]
			
		elif res['status'] == 'error':
			msg = res['value'][0] + ": " + res['value'][1]
			raise OroServerError(msg)
		
		else:
			raise OroServerError("Got an unexpected message status from ORO: " + \
			res['status'])
		
	def add_methods(self, m):
		def innermethod(*args):
			req = ["%s" % m[0]]
			for a in args:
				req.append(str(a))
			logger.log(4, "Sending request: " + req[0])
			return self.call_server(req)
				
		innermethod.__doc__ = "This method is a proxy for the oro-server %s method." % m[0]
		innermethod.__name__ = m[0]
		setattr(self,innermethod.__name__,innermethod)
	
	def close(self):
		self._running = False
		self.join()
		logger.log(4, 'Closing the connection to ORO...')
		self._oro_server.close()
		self.s.close()
		logger.log(4, 'Done. Bye bye!')
	
	def __del__(self):
		if self._oro_server:
			self.close()

if __name__ == '__main__':


	console = logging.StreamHandler()
	console.setLevel(DEBUG_LEVEL)

	# set a format which is simpler for console use
	formatter = logging.Formatter('%(asctime)-15s %(name)s: %(levelname)s - %(message)s')
	# tell the handler to use this format
	console.setFormatter(formatter)
	# add the handler to the root logger
	logger.addHandler(console)
	
	HOST = 'localhost'	# ORO-server host
	PORT = 6969		# ORO-server port
	
	oro = Oro(HOST, PORT)
	
	def printer(c):
		print "Yeahh! event content: " + str(c)
	
	logger.info("Starting now...")
	try:
		oro.subscribe(["?o isIn room"], printer)
		
		#oro.processNL("learn that today is sunny")
		#oro.add(["johnny rdf:type Human", "johnny rdfs:label \"A que Johnny\""])
		
		#if oro.check("[johnny rdf:type Human, johnny rdfs:label \"A que Johnny\"]"):
		#	print "Yeaaaah"
		
		
		#oro.addForAgent("johnny", "[hrp2 rdf:type Robot]")
		#print(oro.lookup("A que Johnny")[0])
		
		#for r in oro.find("bottle", "[?bottle rdf:type Bottle]"):
		#	print r

		
		oro.add(["tutuo isIn room"])
		
		time.sleep(1)
		
		logger.info("done!")
		
		
	except OroServerError as ose:
		print('Oups! An error occured!')
		print(ose)
	finally:
		oro.close()
