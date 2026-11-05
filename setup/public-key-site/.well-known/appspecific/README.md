# Public key file

The published file must use this exact name, because that is the path the vehicle account fetches:

`com.tesla.3p.public-key.pem`

Put only the public key there. Do not put the private key in this folder. The publish script copies the public key into `<username>.github.io`. It does not commit the key into this AgentDrive checkout. If that repository already hosts a site, the script adds this file and does not replace the rest of the site. An empty `.nojekyll` file is added when it is missing so GitHub Pages serves `.well-known`.
