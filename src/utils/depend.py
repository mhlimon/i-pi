import numpy as np
import pdb, time

# if A depends upon B, then A.dep_up-->B and B.dep_dw-->A
class depend_proxy(object):
   """Prototype class for dependency handling"""
   def __init__(self, dependants=[], dependencies=[], linkto=None):
      self._dependants=[]
      self._tainted=False
      self._linkto=linkto
      for item in dependencies:
         item.add_dependant(self)
      for item in dependants:
         self.add_dependant(item)
   
   def link_value(self, value):
      self._linkto=value
   
   def add_dependant(self,newdep):
      """Makes newdep dependent on self"""
      self._dependants.append(newdep)
      newdep.taint(taintme=True)      

   def add_dependency(self,newdep):
      """Makes self dependent on newdep"""
      newdep._dependants.append(self)      
      self.taint(taintme=True)

   def taint(self,taintme=True):
      """Recursively sets tainted flag on dependent objects."""
      #if not self._linkto is None and self._linkto.name == "vir": pdb.set_trace()      
      self._tainted = True     #this is to prevent circular dependencies to hang forever
      for item in self._dependants: 
         if (not item.tainted()):  item.taint()
      self._tainted = taintme
      
   def tainted(self):
      """Returns tainted flag"""
      return self._tainted
      
   def update_auto(self): pass

   def update_man(self): self.taint(taintme=False)
   

class depend_func(depend_proxy):
   """Proxy which defines a function to compute the value of the property. 
      Setting the property manually is forbidden."""
   def __init__(self, func, dependants=[], dependencies=[], linkto=None):
      self._func=func
      super(depend_func, self).__init__(dependants=dependants, dependencies=dependencies, linkto=linkto)
         
   def update_auto(self):
      self._linkto.set(self._func(), manual=False)

   def update_man(self):
      print "Cannot set manually the value of the automatically-computed property <",self._linkto.name,">"
      raise NameError(self._linkto.name)
      
class synchronizer(object):
   def __init__(self, deps=None):
      if deps is None:
         self._synced=dict()
      else:
         self._synced=deps
         
      self._manual=None

class depend_sync(depend_proxy):
   """Proxy which allows to keep different representations of the same 
      quantity synchronized. Only one can be set manually."""
   def __init__(self, func, synchro, dependants=[], dependencies=[], linkto=None):
      self._func=func
      self.synchro=synchro
      super(depend_sync, self).__init__(dependants=dependants, dependencies=dependencies, linkto=linkto)

   def link_value(self, value):
      self._linkto=value
      self.synchro._synced[value.name]=self
      self.synchro._manual=value.name
      
   def taint(self,taintme=True):
      """Recursively sets tainted flag on dependent objects."""
      super(depend_sync,self).taint(taintme)
      # Also taints object within the sync group, making sure that the one which is manually set is not tainted
      self._tainted=True
      for v in self.synchro._synced.values():
         if (not v.tainted()) and (not v is self): v.taint(taintme=True)
      self._tainted=(taintme and (not self._linkto.name == self.synchro._manual))
               
   def update_auto(self):
      if (not self._linkto.name == self.synchro._manual):
         self._linkto.set(self._func[self.synchro._manual](), manual=False)

   def update_man(self):         
      for v in self.synchro._synced.values():
         v.taint(taintme=True)
      self.synchro._manual=self._linkto.name
      self._tainted=False
      
class depend_base(object):
   def __init__(self, deps=None, name=None, tainted=True):
      self.name=name
      if deps==None:
         self.deps=depend_proxy()
      else:
         self.deps=deps
      if (self.deps._linkto is None): self.deps.link_value(self)
      if (tainted) : self.deps.taint(taintme=tainted)  # ALWAYS taint on init      

class depend_value(depend_base):
   def __init__(self, value=None, deps=None, name=None, tainted=True):
      self._value=value
      super(depend_value,self).__init__(deps, name, tainted)

   def get(self):
      if self.deps.tainted():  
         self.deps.update_auto()
         self.deps.taint(taintme=False)
         
      return self._value
      
   def __get__(self, instance, owner):
      return self.get() 

   def set(self, value, manual=True):
      self._value=value
      self.deps.taint(taintme=False)
      if (manual) : self.deps.update_man()
      
   def __set__(self, instance, value): 
      self.set(value)   

#   def __str__(self):
#      return str(self.get())

class depend_array(np.ndarray, depend_base):
   def __new__(cls, value, deps=None, name=None, tainted=True):
#      print "new", name, cls
      # Input array is an already formed ndarray instance
      # We first cast to be our class type
      obj = np.asarray(value).view(cls)
      return obj
      
   def __init__(self, value, deps=None, name=None, tainted=True):
#      print "init", name
      super(depend_array,self).__init__(deps=deps, name=name, tainted=tainted)
#      if self.deps._linkto is None: self.deps._linkto = self
   
   def __array_finalize__(self, obj):  
#      print "finalize", type(self), type(obj),  hasattr(self,"name"),  hasattr(obj,"name")
      # makes sure that --if we really mean to return a deparray-- some basic dep things are provided
      self.name=None
      self.deps=depend_proxy()
      pass
   
   # whenever possible in compound operations just return a regular ndarray
   __array_priority__=-1.0  
#   def __array_wrap__(self, out_arr, context=None):
#      print "array_wrap", self.name, out_arr.name #, context
#      return super(depend_array,self).__array_wrap__(self, out_arr, context)      
#      return super(depend_array,self).__array_wrap__(self, out_arr, context).view(np.ndarray)

#   def __array_prepare__(self, out_arr, context=None):        
#      print "array_prepare"
#      return super(depend_array,self).__array_prepare__(self, out_arr, context).view(np.ndarray)
      
   def __getitem__(self,index):
#      print "getitem", self.name, self.deps.tainted(), index
      
      if self.deps.tainted():  
         self.deps.update_auto()
         self.deps.taint(taintme=False)
              
      if (not np.isscalar(index) or self.ndim > 1 ):
#         return depend_array(super(depend_array,self).__getitem__(index), deps=self.deps, name=self.name, tainted=self.deps._tainted)  
         return depend_array(self.view(np.ndarray)[index], deps=self.deps, name=self.name, tainted=self.deps._tainted)  
      else:
         return self.view(np.ndarray)[index]

   def __getslice__(self,i,j):
      return self.__getitem__(slice(i,j,None))

   def get(self):
      return self.__getitem__(slice(None,None,None))
            
   def __get__(self, instance, owner):
      return self.__getitem__(slice(None,None,None))

   def __setitem__(self,index,value,manual=True):      
      #print "setitem", manual, self.name, self.deps._tainted    
      
      if (manual) : self.deps.update_man()
      self.deps.taint(taintme=False)      
      #super(depend_array,self).__setitem__(index,value)   # directly write to the base array
      self.view(np.ndarray)[index]=value

   def __setslice__(self,i,j,value):
      return self.__setitem__(slice(i,j),value)

   def set(self, value, manual=True):
      self.__setitem__(slice(None,None),value=value,manual=manual)    

   def __set__(self, instance, value): 
      self.__setitem__(slice(None,None),value=value)

#   def __str__(self):
#      return str(self.get())

def dget(obj,member):
   return obj.__dict__[member]
def dset(obj,member,value):
   obj.__dict__[member]=value

def depstrip(array):
   return array.view(np.ndarray)
def depget(obj,member):
   return obj.__dict__[member].deps
def depset(obj, member, dep):
   obj.__dict__[member].deps=dep   

def depcopy(objfrom,memberfrom,objto,memberto):
   dfrom=dget(objfrom,memberfrom); dto=dget(objto,memberto);
   if not type(dfrom) is type(dto) : raise TypeError("Cannot copy between depend storage of different type")
   depfrom=depget(objfrom,memberfrom); depto=depget(objto,memberto);
   if not type(depfrom) is type(depto) : raise TypeError("Cannot copy between depend proxies of different type")   
   
   for d in depto._dependants: depfrom.add_dependant(d)
   if depfrom._linkto is None: depfrom._linkto=depto._linkto
   dset(objto,memberto,dfrom)


#time.doprint=False
class dobject(object): 
   def __getattribute__(self, name):
      #if time.doprint: print "getattr", name
      value = object.__getattribute__(self, name)
      if hasattr(value, '__get__'):
         value = value.__get__(self, self.__class__)
      return value

   def __setattr__(self, name, value):
      try:
         obj = object.__getattribute__(self, name)
      except AttributeError:
         pass
      else:
         if hasattr(obj, '__set__'):
            return obj.__set__(self, value)
      return object.__setattr__(self, name, value)
