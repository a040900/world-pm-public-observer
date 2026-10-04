import sys
from pathlib import Path

def forbid_network(event, args):
    if event in ('socket.connect', 'socket.sendto', 'socket.getaddrinfo'):
        with Path('/workspace/scratch/ce64e7c945e3/offline-guard/blocked-events.log').open('a') as log:
            log.write(event + '\n')
        raise RuntimeError('CLOUD_OFFLINE_NETWORK_FORBIDDEN')
sys.addaudithook(forbid_network)
