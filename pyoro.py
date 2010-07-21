#!/usr/bin/python
import logging
import socket

class OroServerError(Exception):
	def __init__(self, value):
		self.value = value
	def __str__(self):
		return repr(self.value)
	
class Oro(object):
	def __init__(self, host, port):
		
		self.server = None
		
		try:
			#create an INET, STREAMing socket
			self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

			#now connect to the oro server
			self.s.connect((host, port))
			self.server = self.s.makefile()
		except socket.error:
			self.s.close()
			raise OroServerError('Unable to connect to the server. Check it is running and ' + \
								 'that you provided the right host and port.')
		
		#get the list of methods currenlty implemented by the server
		try:
			res = self.call_server(["listSimpleMethods"])
			self.rpc_methods = [(t.split('(')[0], len(t.split(','))) for t in res]
		except OroServerError:
			self.server.close()
			self.s.close()
			raise OroServerError('Cannot initialize the oro connector! Smthg wrong with the server!')
		
		#add the the Oro class all the methods the server declares
		for m in self.rpc_methods:
			self.add_methods(m)

	def __del__(self):
		if self.server:
			self.close()
	
	def call_server(self, req):
		for r in req:		
			self.server.write(r)
			self.server.write("\n")
			
		self.server.write("#end#\n")
		self.server.flush()
		
		status = self.server.readline().rstrip('\n')
		
		if status == 'ok':
			raw = self.server.readline().rstrip('\n')
			self.server.readline() #remove the trailing #end#
			if raw == '':
				return
			#special case for boolean that can not be directly evaluated by Python
			#since the server return true/false in lower case
			if raw.lower() == 'true':
				return True
			if raw.lower() == 'false':
				return False
				
			res = eval(raw)
			return res
		else:
			msg = self.server.readline().rstrip('\n') + ": " + self.server.readline().rstrip('\n')
			self.server.readline() #remove the trailing #end#
			raise OroServerError(msg)
		
	def add_methods(self, m):
		def innermethod(*args):
			req = ["%s" % m[0]]
			for a in args:
				req.append(str(a))
			return self.call_server(req)
				
		innermethod.__doc__ = "This method is a proxy for the oro-server %s method." % m[0]
		innermethod.__name__ = m[0]
		setattr(self,innermethod.__name__,innermethod)
	
	def close(self):
		logging.debug('Closing the connection to ORO...')
		self.server.close()
		self.s.close()
		logging.debug('Done. Bye bye!')



if __name__ == '__main__':

	HOST = 'localhost'	# ORO-server host
	PORT = 6969		# ORO-server port

	try:
		oro = Oro(HOST, PORT)
		#oro.processNL("learn that today is sunny")
		oro.add(["johnny rdf:type Human", "johnny rdfs:label \"A que Johnny\""])
		
		if oro.check("[johnny rdf:type Human, johnny rdfs:label \"A que Johnny\"]"):
			print "Yeaaaah"
		
		
		#oro.addForAgent("hum1", "[hrp2 rdf:type Robot]")
		#print(oro.lookup("A que Johnny")[0])
		
		#for r in oro.find("bottle", "[?bottle rdf:type Bottle]"):
		#	print r

		#print(oro.getSimilarities("johnny", "hrp2"))
		#print(oro.getDifferences("johnny", "hrp2"))
	except OroServerError as ose:
		print('Oups! An error occured!')
		print(ose)
	finally:
		oro.close()
