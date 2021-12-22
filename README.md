# Distributed website analyzer and geolocation service.

This project contains the following files:

* cloud.py - Info on physical location of Amazon and Google datacenters.
* socketutil.py - Helper functions to make working with Phython sockets a little easier.
* fabfile.py - Fabric3 cloud deployment script.
* geoanalyze.py - main function that just calls worker.py and central.py.
* central.py - Central server that opens/listens at port 8080
* worker.py - Worker function that receives url and calculates rtt estimates

