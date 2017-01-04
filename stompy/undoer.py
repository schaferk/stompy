""" Generic support for recording operations, with the option
of undoing those operations.
"""

class OpHistory(object):
    state='inactive' # 'recording','reverting'

    class Checkpoint(object):
        def __init__(self,serial,frame):
            self.serial=serial
            self.frame=frame

    # Undo-history management - very generic.
    op_stack_serial = 17
    op_stack = None
    def checkpoint(self):
        assert self.state != 'reverting'

        if self.op_stack is None:
            self.op_stack_serial += 1
            self.op_stack = []
        self.state='recording'
        return self.Checkpoint(self.op_stack_serial,len(self.op_stack))

    def revert(self,cp):
        if cp.serial != self.op_stack_serial:
            raise ValueError( ("The current op stack has serial %d,"
                               "but your checkpoint is %s")%(self.op_stack_serial,
                                                             cp.serial) )
        if self.state!='recording':
            raise Exception("Tried to revert, but not recording")
        try:
            self.state='reverting'
            while len(self.op_stack) > cp.frame:
                self.pop_op()
        finally:
            self.state='recording'

    def commit(self):
        assert self.state != 'reverting'
        self.op_stack = None
        self.op_stack_serial += 1
        self.state='inactive'
    
    def push_op(self,meth,*data,**kwdata):
        if self.state!='recording':
            return

        if self.op_stack is not None:
            self.op_stack.append( (meth,data,kwdata) )

    def pop_op(self):
        assert self.state=='reverting'

        f = self.op_stack.pop()
        self.log.debug("popping: %s"%( str(f) ) )
        meth = f[0]
        args = f[1]
        kwargs = f[2]
        
        meth(*args,**kwargs)
        
    def __getstate__(self):
        try:
            d=super(OpHistory,self).__getstate__()
        except AttributeError:
            d = dict(self.__dict__)

        d['op_stack']=None
        d['state']='inactive'

        return d
