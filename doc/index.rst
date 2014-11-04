pykb: A Python API to access a KB-API conformant knowledge base
===============================================================

Package documentation
---------------------

.. toctree::
    :maxdepth: 2
    
    api/kb.rst


Examples
--------


.. code:: python

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

