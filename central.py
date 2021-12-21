# Kevin E + Tim W + Liam D , 11/5/20
# central server, opens/listens at port 8080 
# sends urls to workers, calculates average from rtts, 
# finds minimum + displays on webpage

#!/usr/bin/env python3
import os            # for os.path.isfile()
import socket        # for socket stuff
import socketutil
import sys           # for sys.argv
import urllib.parse  # for urllib.parse.unquote()
import time          # for time.time()
import threading     # for threading.Thread()
import re            # for regex split()
import random        # for random numbers
import string        # for various string operations

import cloud

# Global configuration variables, with default values.
server_host = "" 
server_port = 8080
server_root = "./web_files"

avg_rtt = []
location = []
workers = []
coord = []
ips = []

# Global variables to keep track of statistics, with initial values. These get
# updated by different connection handler threads. To avoid race conditions,
# these should only be accessed within a "with" block, like this:
#     with stats.lock:
#        stats.tot_time += x
#        if stats.max_time < x:
#            ...
class Statistics:
    def __init__(self):
        self.total_connections = 0
        self.active_connections = 0
        self.num_requests = 0
        self.num_errors = 0
        self.tot_time = 0 # total time spent handling requests
        self.avg_time = 0 # average time spent handling requests
        self.max_time = 0 # max time spent handling a request
        self.lock = threading.Condition()
stats = Statistics()


# Request objects are used to hold information associated with a single HTTP
# request from a client.
class Request:
    def __init__(self):
        self.method = ""  # GET, POST, PUT, etc. for this request
        self.path = ""    # url path for this request
        self.version = "" # http version for this request
        self.headers = [] # headers from client for this request
        self.length = 0   # length of the request body, if any
        self.body = None  # contents of the request body, if any


# Response objects are used to hold information associated with a single HTTP
# response that will be sent to a client. The code is required, and should be
# something like "200 OK" or "404 NOT FOUND". The mime_type and body are
# options. If present, the mime_type should be something like "text/plain" or
# "image/png", and the body should be a string or raw bytes object containing
# contents appropriate for that mime type.
class Response:
    def __init__(self, code, mime_type=None, body=None):
        self.code = code
        self.mime_type = mime_type
        self.body = body


# Connection objects are used to hold information associated with a single HTTP
# connection socket, like the socket itself, statistics, any leftover data from
# the client that hasn't yet been processed, etc.
class Connection:
    def __init__(self, c, addr):
        self.sock = c             # the socket connected to the client
        self.client_addr = addr   # address of the client
        self.leftover_data = b""  # data from client, not yet processed
        self.num_requests = 0     # number of requests from client handled so far
        #can try keepalive header here

    # read_until_blank_line() returns data from the client up to (but not
    # including) the next blank line, i.e. "\r\n\r\n". The "\r\n\r\n" sequence
    # is discarded. Any leftovers after the blank line is saved for later. This
    # function returns None if an error is encountered.
    def read_until_blank_line(self):
        data = self.leftover_data
        try:
            while b"\r\n\r\n" not in data:
                # Read (up to) another 4KB of data from the client
                more_data = self.sock.recv(4096)
                if not more_data: # Connection has died?
                    self.leftover_data = data # save it all for later
                    return None
                data = data + more_data
            # The part we want is everything up to the first blank line.
            data, self.leftover_data = data.split(b"\r\n\r\n", 1)
            return data.decode()
        except:
            log("Error reading from client %s socket" % (self.client_addr))
            self.leftover_data = data # save it all for later
            return None

    # read_amount(n) returns the next n bytes of data from the client. Any
    # leftovers after the n bytes are saved for later. This function returns
    # None if an error is encountered.
    def read_amount(self, n):
        data = self.leftover_data
        try:
            while len(data) < n:
                more_data = self.sock.recv(n - len(data))
                if not more_data: # Connection has died?
                    self.leftover_data = data # save it all for later
                    return None
                data = data + more_data
            # The part we want is the first n bytes.
            data, self.leftover_data = (data[0:n], data[n:])
            return data.decode()
        except:
            log("Error reading from client %s socket" % (self.client_addr))
            self.leftover_data = data # save it all for later
            return None


# log(msg) prints a message to standard output. Since multi-threading can jumble
# up the order of output on the screen, we print out the current thread's name
# on each line of output along with the message.
# Example usage:
#   log("Hello %s, you are customer number %d, have a nice day!" % (name, n))
def log(msg):
    # Convert msg to a string, if it is not already
    if not isinstance(msg, str):
        msg = str(msg)
    # Each python thread has a name. Use current thread's in the output message.
    myname = threading.current_thread().name
    # When printing multiple lines, indent each line a bit
    indent = (" " * len(myname))
    linebreak = "\n" + indent + ": "
    lines = msg.splitlines()
    msg = linebreak.join(lines)
    # Print it all out, prefixed by this thread's name.
    print(myname + ": " + msg)


# get_header_value() finds a specific header value from within a list of header
# key-value pairs. If the requested key is not found, None is returned instead.
# The headers list comes from an HTTP request sent from the client. The key
# should usually be a standard HTTP header, like "Content-Type",
# "Content-Length", "Connection", etc.
def get_header_value(headers, key):
    for hdr in headers:
        if hdr.lower().startswith(key.lower() + ": "):
            val = hdr.split(" ", 1)[1]
            return val
    return None


# make_printable() does some conversions on a string so that it prints nicely
# on the console while still showing unprintable characters (like "\r") in 
# a sensible way.
printable = string.ascii_letters + string.digits + string.punctuation + " \r\n\t"
def make_printable(s):
    s = s.replace("\n", "\\n\n")
    s = s.replace("\t", "\\t")
    s = s.replace("\r", "\\r")
    s = s.replace("\r", "\\r")
    return ''.join(c if c in printable else r'\x{0:02x}'.format(ord(c)) for c in s)

# handle_one_http_request() reads one HTTP request from the client, parses it,
# decides what to do with it, then sends an appropriate response back to the
# client. 
def handle_one_http_request(conn):
   
    # The HTTP request is everything up to the first blank line
    data = conn.read_until_blank_line()
    if data == None:
        return # something is wrong, maybe connection was closed by client?

    log("Request %d has arrived...\n%s" % (conn.num_requests, make_printable(data+"\r\n\r\n")))

    # Make a Request object to hold all the info about this request
    req = Request()

    # The first line is the request-line, the rest is the headers.
    lines = data.splitlines()
    if len(lines) == 0:
        log("Request is missing the required HTTP request-line")
        resp = Response("400 BAD REQUEST", "text/plain", "You need a request-line!")
        send_http_response(conn, resp)
        return
    request_line = lines[0]
    req.headers = lines[1:]

    # The request-line can be further split into method, path, and version.
    words = request_line.split()
    if len(words) != 3:
        log("The request-line is malformed: '%s'" % (request_line))
        resp = Response("400 BAD REQUEST", "text/plain", "Your request-line is malformed!")
        send_http_response(conn, resp)
        return
    req.method = words[0]
    req.path = words[1]
    req.version = words[2]

    log("Request has method=%s, path=%s, version=%s, and %d headers" % (
        req.method, req.path, req.version, len(req.headers)))

    # The path will look like either "/foo/bar" or "/foo/bar?key=val&baz=boo..."
    # Unmangle any '%'-signs in the path, but just the part before any '?'-mark
    
    if "?" in req.path:
        req.path, params = req.path.split("?", 1)
        req.path = urllib.parse.unquote(req.path) + "?" + params
    else:
        req.path = urllib.parse.unquote(req.path)

    # Browsers that use chunked transfer encoding are tricky, don't bother.
    if get_header_value(req.headers, "Transfer-Encoding") == "chunked":
        log("The request uses chunked transfer encoding, which isn't yet supported")
        resp = Response("411 LENGTH REQUIRED", "text/plain", "Your request uses chunked tranfer encoding, sorry!")
        send_http_response(conn, resp)
        return

    # If request has a Content-Length header, get the body of the request.
    
    n = get_header_value(req.headers, "Content-Length")
    if n is not None:
        req.length = int(n)
        req.body = conn.read_amount(int(n))

    keep_check = get_header_value(req.headers, "Connection").lower()
    if  keep_check == "keep-alive":
        conn.keepAlive = True
    else:
        conn.keepAlive = False
      
    # Finally, look at the method and path to decide what to do.
    if req.method == "GET":
        resp = handle_http_get(req,conn)
    elif req.method == "POST":
        resp = handle_http_post(req)
    else:
        log("Method '%s' is not recognized or not yet implemented" % (req.method))
        resp = Response("405 METHOD NOT ALLOWED",
                "text/plain",
                "Unrecognized method: " + req.method)

    # Now send the response to the client.
    send_http_response(conn, resp)
    
# send_http_response() sends an HTTP response to the client. The response code
# should be something like "200 OK" or "404 NOT FOUND". The mime_type and body
# are sent as the contents of the response.
def send_http_response(conn, resp):
 
    # If this is anything other than code 200, tally it as an error.
    if not resp.code.startswith("200 "):
        with stats.lock: # update overall server statistics
            stats.num_errors += 1
    # Make a response-line and all the necessary headers.
    data = "HTTP/1.1 " + resp.code + "\r\n"
    data += "Server: csci356\r\n"
    data += "Date: " + time.strftime("%a, %d %b %Y %H:%M:%S %Z") + "\r\n"

    body = None
    if resp.mime_type == None:
        data += "Content-Length: 0\r\n"
    else:
        if isinstance(resp.body, bytes):   # if response body is raw binary...
            body = resp.body               # ... no need to encode it
        elif isinstance(resp.body, str):   # if response body is a string...
            body = resp.body.encode()      # ... convert to raw binary
        else:                              # if response body is anything else...
            body = str(resp.body).encode() # ... convert it to raw binary
        data += "Content-Type: " + resp.mime_type + "\r\n"
        data += "Content-Length: " + str(len(body)) + "\r\n"
        if conn.keepAlive is True:
            data += "Connection: keep-alive\r\n" #trying keepalive stuff here
        else: 
            data += "Connection: close\r\n"
    data += "\r\n"

    # Send response-line, headers, and body
    log("Sending response-line and headers...\n%s" % (make_printable(data)))
    conn.sock.sendall(data.encode())
    if body is not None:
        log("Response body (not shown) has %d bytes, mime type '%s'" % (len(body), resp.mime_type))
        conn.sock.sendall(body)




# handle_http_get_hello() returns a response for GET /hello
def handle_http_get_hello(bgcolor, username):
    if username == "/hello": #if no username 
        username = ""
    msg = "<html><head><title>Hello World!</title></head>"
    msg += "<body style = background-color:%s>" % bgcolor
    msg += '<h1>Hello %s hit page refresh (F5) to refresh this page, <br>'% username
    msg += 'though the contents will never change, sadly.</h1><br> '
    msg += '<p> You can also go to these exciting pages:<br>' 
    msg += "</p></body></html>"

    return Response("200 OK", "text/html", msg)

def http_get_index():
    msg = "<html><head><title>Geolocation Service</title></head>"
    msg += "<body>" 
    msg += "<h3>Welcome to Kevin's, Tim's, and Liam's geolocation service</h3>"
    msg += "<p></p>"
    msg += "<p></p>"
    msg += "<form action='/analyze' method='GET'>"
    msg += "Enter a url and we will try estimate its physical location (HTTPS is not supported yet, only HTTP so far, sorry):<br>"
    msg += "<input type='text' name='target' size='80' value='http://www.google.com/'>"
    msg += "<input type='submit'>"
    msg += "</form>"
    msg += "</p>"
    msg += "</body></html>"

    return Response("200 OK", "text/html", msg)


def location_page(): #def location_page(rtt_list):
    msg = "<html><head><title>Geolocation Service</title></head>"
    msg += "<body>" 
    msg += "<h3>Kevin, Tim, and Liam are trying to estimate your location</h3>"
    msg += "<p> We'll do something with our workers RTTs and give you an estimate soon</p>"
    msg += "</body></html>"

    return Response("200 OK", "text/html", msg)

# send msg to each worker containing url
# recv msg conatining result from each worker
# join results together into single page
# msg = combined results
def http_get_analyze(path,conn,req):
    global location
    global avg_rtt
    global workers
    global coord
    global ips
    
    url = urllib.parse.unquote(path.split('=')[1])

    #trying to send a message to all workers
    if len(url) != 0:
        for i in workers: 
            i.sendall(url + "\n")
           
    min_rtt = 100000

    msg = "<html><head><title>Geolocation Service</title></head>"
    msg += "<body>" 
    msg += "<h3>Kevin, Tim, and Liam are trying to find your link</h3>"
    msg += "<form action='/analyze' method='GET'>"
    msg += "Enter a url (HTTPS is not supported yet, only HTTP so far, sorry):<br>"
    msg += "<input type='text' name='target' size='80'>"
    msg += "<input type='submit'>"
    msg += "</form>"

    
    for i in range(len(workers)):
        msg += "<h2> The RTT from %s %s is: %s seconds</h2>" % (location[i], coord[i], avg_rtt[i]) #location[0][0]
    count = 0
    #calc min average from rtt_list
    for i in range(len(avg_rtt)):
        if (avg_rtt[i] < min_rtt):
            min_rtt = avg_rtt[i]
            count = i
    msg += "<h2> Based on the minimum RTT, your location is at %s with coordinates %s  and IP %s </h2>" %(location[count], coord[count], ips[count])
    msg += "</body></html>"
        
    return Response("200 OK", "text/html", msg)

#register worker called upon worker.py
def http_register_worker(conn, body):
    global workers
    global location
    global coord
    global avg_rtt
    global ips
    print("trying to register client")
    if sock not in workers:
        workers.append(sock)
        avg_rtt.append(None)
        ips.append(None)
    msg = ""

    if "worker_info" in body:
        res = body.split(':')
        res = res[-1].split("'")
        loc = res[1]
        co = res[-1].split("]")
        co = co[0].split("'")
        co = str(co[0][2:])
        if loc not in location:
            location.append(loc)
            coord.append(co)


    return Response("acknowledged", "text/html", msg)
    

# handle_http_get_file() returns an appropriate response for a GET request that
# seems to be for a file, rather than a special URL. If the file can't be found,
# or if there are any problems, an error response is generated.
def handle_http_get_file(url_path):
    log("Handling http get file request, for "+ url_path)
    file_path = server_root + url_path

    # There is a very real security risk that the requested file_path could
    # include things like "..", allowing a malicious or curious client to access
    # files outside of the server's web_files directory. We take several
    # precautions here to make sure that there is no funny business going on.
    # First security precaution: "normalize" to eliminate ".." elements
    file_path = os.path.normpath(file_path)
    file_type = ''.join(url_path).split('.')[-1] 


    # Third security precaution: check if the path is actually a file
    if not os.path.isfile(file_path):
        log("File was not found: " + file_path)
        return Response("404 NOT FOUND", "text/plain", "No such file: " + url_path)

    # Finally, attempt to read data from the file, and return it
    try:
        with open(file_path, "rb") as f: # "rb" mode means read "raw bytes"
            data = f.read()

        if file_type == "png":
            mime_type = "image/png"
        elif file_type == "jpg" or file_type == "jpeg":
            mime_type = "image/jpeg"
        elif file_type == "html":
            mime_type = "text/html"
        elif file_type == "css":
            mime_type = "text/css"
        elif file_type == "js":
            mime_type = "application/javascript"
        elif file_type == "txt":
            mime_type = "text/plain"
        return Response("200 OK", mime_type, data)
    except:
        log("Error encountered reading from file")
        return Response("403 FORBIDDEN", "text/plain", "Permission denied: " + url_path)


# handle_http_get() returns an appropriate response for a GET request
def handle_http_get(req,conn):
    global avg_rtt
    global location
    global workers
    global coord

    rtt_times = [0]

    username = req.path.split('=') #prev project, not needed
    req.start = username[0].split('?')[0] #finds path before variables
    # Generate a response
    if req.start == "/hello":
        color = ["#"+''.join([random.choice('0123456789ABCDEF') for j in range(6)]) for i in range(1)] #random hex color
        resp = handle_http_get_hello(color[0],username[-1])
    elif req.start == "/analyze":
        resp = http_get_analyze(req.path,conn,req)
    elif req.path == "/register_worker":
        resp = http_register_worker(conn, req.body)
    elif req.path == "/rtt-time":

        first = req.body.split('\r\n')
        loc = first[0]

        if "worker_info" in loc:
            res = loc.split(':')
            res = res[-1].split("'")
            loc = res[1]

        #if in location, add ip + calculate avg rtt
            if loc in location:
                index = location.index(loc)
                rtt_times = first[1].split(':')[-1].strip('][').split(', ')
                rtt_times[0] = rtt_times[0][2:]

                ip = first[-2]
                ip = ip.split(":")
                
                ips[index] = ip[-1]
            #calc average rtt

            average = 0
            if(len(rtt_times) > 0):
                for i in range(len(rtt_times)):
                    temp_rtt = float(rtt_times[i])
                    average += temp_rtt

                if len(avg_rtt) <= len(workers):
                    if len(workers) - len(avg_rtt) == 1 or len(avg_rtt) == 0:
                        avg_rtt.append(average/len(rtt_times))

                    avg_rtt[index] = average/len(rtt_times)
            

        resp = location_page()
    elif req.start == "/" or req.start == "/index":
        resp = http_get_index()
    else:
        resp = handle_http_get_file(req.path)
    return resp


# handle_http_connection() reads one or more HTTP requests from a client, parses
# each one, and sends back appropriate responses to the client.
def handle_http_connection(conn):
    conn.keepAlive = True
    
    with stats.lock: # update overall server statistics
        stats.active_connections += 1
    log("Handling connection from " + str(conn.client_addr)) 
    
    try:
        # Process one HTTP request from client
        while conn.keepAlive is True:
            start = time.time()
            conn.keepAlive = False
            handle_one_http_request(conn)
            end = time.time()
            duration = end - start
            
            # Do end-of-request statistics and cleanup
            conn.num_requests += 1 # counter for this connection
            log("Done handling request %d from %s" % (conn.num_requests, conn.client_addr))
            with stats.lock: # update overall server statistics
                stats.num_requests += 1
                stats.tot_time = stats.tot_time + duration
                stats.avg_time = stats.tot_time / stats.num_requests
                if duration > stats.max_time:
                    stats.max_time = duration
                
    finally:
        
        conn.sock.close() #keepalive keeps this open
        log("Done with connection from " + str(conn.client_addr))
        with stats.lock: # update overall server statistics
            stats.active_connections -= 1
    

# This remainder of this file is the main program, which listens on a server
# socket for incoming connections from clients, and starts a handler thread for
# each one.

# Print a welcome message
server_addr = (server_host, server_port)
log("Starting web server")
log("Listening on address %s:%d" % (server_host, server_port))
log("Serving files from %s" % (server_root))
log("Ready for connections...")

# Create the server socket, and set it up to listen for connections
s = socketutil.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(server_addr)
s.listen(5)

try:
    # Repeatedly accept and handle connections
    while True:
        sock, client_addr = s.accept()
        print("got connection from client %s:%s" % (client_addr))
        
        
        # A new client socket connection has been accepted. Count it.
        with stats.lock:
            stats.total_connections += 1
        # Put the info into a Connection object.
        conn = Connection(sock, client_addr)
        # Start a thread to handle the new connection.
        t = threading.Thread(target=handle_http_connection, args=(conn,))
        t.daemon = True
        t.start()
finally:
    log("Shutting down...")
    s.close()

log("Done")