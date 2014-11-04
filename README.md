pykb: A Python API to access a KB-API conformant knowledge base
===============================================================

[![Documentation Status](https://readthedocs.org/projects/pykb/badge/?version=latest)](http://pykb.readthedocs.org)

`pykb` provides a 'pythonic' interface to
[KB-API](http://homepages.laas.fr/slemaign/wiki/doku.php?id=kb_api_robotics)
conformant knowledge bases like
[minimalkb](https://github.com/chili-epfl/minimalkb) or
[ORO](https://github.com/severin-lemaignan/oro-server).

Installation
------------

```
$ pip install pykb
```

(or, of course, from the source: clone & `python setup.py install`)

Documentation
-------------

[Head to readthedocs](http://pyrobots.readthedocs.org). Sparse for now.


Examples
--------

```python

import kb
import time

REASONING_DELAY = 0.2

def onevent(evt):
    print("Something happened! %s" % evt)

with kb.KB() as kb:

    kb += ["alfred rdf:type Human", "alfred likes icecream"]
    
    if 'alfred' in kb:
        print("Hello Alfred!")

    if 'alfred likes icecream' in kb:
        print("Oh, you like icrecreams?")

    kb -= ["alfred likes icecream"]

    if 'alfred likes *' not in kb:
        print("You don't like anything? what a pity...")

    kb += ["Human rdfs:subClassOf Animal"]
    time.sleep(REASONING_DELAY) # give some time to the reasoner
    
    if 'alfred rdf:type Animal' in kb:
        print("I knew it!")

    for facts in kb.about("Human"):
        print(facts)

    for known_human in kb["?human rdt:type Human"]:
        print(known_human)


    kb += ["alfred desires jump", "alfred desires oil"]
    kb += ["jump rdf:type Action"]

    for action_lover in kb["?agent desires ?obj", "?obj rdf:type Action"]:
        print(action_lover)

    kb.subscribe(["?agent isIn ?loc", "?loc rdf:type Room"], onevent)
    kb += ["alfred isIn sleepingroom", "sleepingroom rdf:type Room"]

    time.sleep(1) # event should have been triggered!
```
