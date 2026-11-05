# Public key file

The published file must use this exact name, because that is the path the vehicle account fetches:

`com.tesla.3p.public-key.pem`

Put only the public key there. Do not put the private key in this folder. The publish script copies the public key into a temporary folder and uploads that folder. It does not commit the key.
