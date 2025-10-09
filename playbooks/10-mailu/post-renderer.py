#!/usr/bin/env python3
# Helm post-renderer to inject dual-stack configuration into Mailu internal services
# This modifies Service resources to support both IPv4 and IPv6

import sys
import yaml

# Read all YAML documents from stdin
documents = list(yaml.safe_load_all(sys.stdin))

# Process each document
for doc in documents:
    if doc is None:
        continue
    
    # Check if this is a Service resource
    if doc.get('kind') == 'Service':
        metadata = doc.get('metadata', {})
        name = metadata.get('name', '')
    
        if 'spec' not in doc:
            doc['spec'] = {}
        
        doc['spec']['ipFamilyPolicy'] = 'PreferDualStack'
        doc['spec']['ipFamilies'] = ['IPv4', 'IPv6']

# Write all documents back to stdout
yaml.dump_all(documents, sys.stdout, default_flow_style=False, explicit_start=True)
