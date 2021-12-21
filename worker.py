# Kevin E + Tim W + Liam D 
# receives url from central, fetches/calculates 5 rtt estimates and sends to central
# also print web fetched http response in console
#!/usr/bin/env python3

import os, sys, socket 
import socketutil
import time  

import cloud   #include for deployment stuff

#globals
worker_city = cloud.city
worker_coords = cloud.coords


server_host = "54.235.59.70" 
#server_host = "localhost" 
server_port = 8080
server_addr = (server_host, server_port)
rtt_times = []

worker_info = []
worker_info.append(worker_city)
worker_info.append(worker_coords)

def url_fetch(host,path):
    req = "GET " + path + " HTTP/1.1" + "\r\n"
    req += "Host: " + host + "\r\n"
    req += "Accept: text/html\r\nConnection: close\r\n\r\n"

    return(req)

def url_rtt(req, fetching_socket):         
    for i in range(5):
        #take rtt + append
        start = time.time()
        resp = fetching_socket.sendall(req.encode())
        resp = fetching_socket.recv_line()
        end = time.time()
        rtt = end - start
        rtt_times.append(rtt)
    return(rtt_times)

#splits the url from path
def url_splitting(info):

    #splits out unnecessary chars, seps into protocol/host/path
    info_split = info.split("\n")
    url = info_split[0]
    protocol = url.split(":")
    url_protocol = protocol[0]

    h = url.split("//")
    g = h[1]

    host = g.split("/")
        
    url_host = host[0] 
    url_path = host[1]
    #check if no path
    if url_path == '':
        url_path = '/' 
       
    return(host)


print("Connecting to the central server at %s:%d" % (server_host, server_port))
c = socketutil.socket(socket.AF_INET, socket.SOCK_STREAM)
c.connect(server_addr)


print("Sending 1 hello message to server")

msg = "worker_info: " + str(worker_info)  + "\r\n"
cont_len = len(msg.encode('utf-8'))

req = "GET " + "/register_worker" + " HTTP/1.1" + "\r\n"
req += "Host: " + "127.0.0.1" + "\r\n"
req += "Accept: text/html\r\nConnection: keep-alive\r\n"
req += "Content-Type: text/html\r\n"
req += "Content-Length: " + str(cont_len) + "\r\n\r\n"
req += msg


c.sendall(req)

try: 
    while True:
        info = c.recv_line()
        print("server says: %s" % (info))

        # take info and do get request using url
        
        if "www" in info:
            host = url_splitting(info)

            # dns query
            ip_address = socket.gethostbyname(host[0])
            #url_host = ip_address
            url_host = host[0]
            url_path = host[1]
            

            fetching_socket = socketutil.socket(socket.AF_INET, socket.SOCK_STREAM)
            fetching_socket.connect((url_host,80)) #web listens at port 80

            #create get method to fetch url, record rtt times
            req = url_fetch(url_host,url_path)
            rtt_times = url_rtt(req, fetching_socket)

            
            #prepare get method to send rtts to central
            msg = "worker_info: " + str(worker_info)  + "\r\n"
            msg += "rtt_times: " + str(rtt_times) + "\r\n"
            msg += "ip: " + str(ip_address)  + "\r\n"

            
            cont_len = len(msg.encode('utf-8'))
            
            req = "GET " + "/rtt-time" + " HTTP/1.1" + "\r\n"
            req += "Host: " + "127.0.0.1" + "\r\n"
            req += "Accept: text/html\r\nConnection: keep-alive\r\n"
            req += "Upgrade-Insecure-Requests: 1\r\n"
            req += "Content-Type: text/html\r\n"
            req += "Content-Length: " + str(cont_len) + "\r\n\r\n"
            req += msg     #change w rtt_times 
            c.sendall(req)
            rtt_times = []

            
finally:
    print("worker shutting down")
    c.close()