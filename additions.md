# consider these additional resources

* **https://github.com/Senzing/libpostal-data** - Newer libpostal. Does it help with accuracy or speed in the US?
* **https://postalpro.usps.com/address-quality-solutions/zip-4-product** - A resource to help create an API to craft well-formed addresses, including ZIP+4

* New requirement: detect when a search service does not return an address (for exmaple Just in Time, Lewiston Maine), and have the response automatically reverse geocode and conduct a nearest-address search to attempt to return a high-quality street address for the location. 