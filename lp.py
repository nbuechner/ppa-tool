import os
from launchpadlib.launchpad import Launchpad

# Run this script once (as the maubot user) to authorize and save credentials.
# It will print a URL - open it, click "Allow", then press Enter here.
credentials_file = os.path.expanduser("~/.secret/lp.txt")
os.makedirs(os.path.dirname(credentials_file), exist_ok=True)

launchpad = Launchpad.login_with(
    'ubottu-matrix',          # consumer key / app name
    'production',             # use 'staging' for testing
    credentials_file=credentials_file,
    version='devel'
)

print("Logged in as:", launchpad.me.name)
print("Credentials saved to:", credentials_file)
