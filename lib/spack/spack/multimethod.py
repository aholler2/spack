"""This module contains utilities for using multi-methods in
spack. You can think of multi-methods like overloaded methods --
they're methods with the same name, and we need to select a version
of the method based on some criteria.  e.g., for overloaded
methods, you would select a version of the method to call based on
the types of its arguments.

In spack, multi-methods are used to ease the life of package
authors.  They allow methods like install() (or other methods
called by install()) to declare multiple versions to be called when
the package is instantiated with different specs.  e.g., if the
package is built with OpenMPI on x86_64,, you might want to call a
different install method than if it was built for mpich2 on
BlueGene/Q.  Likewise, you might want to do a different type of
install for different versions of the package.

Multi-methods provide a simple decorator-based syntax for this that
avoids overly complicated rat nests of if statements.  Obviously,
depending on the scenario, regular old conditionals might be clearer,
so package authors should use their judgement.
"""
import sys
import functools
import collections

import spack.architecture
import spack.error
from spack.util.lang import *
from spack.spec import parse_anonymous_spec, Spec


class SpecMultiMethod(object):
    """This implements a multi-method for Spack specs.  Packages are
       instantiated with a particular spec, and you may want to
       execute different versions of methods based on what the spec
       looks like.  For example, you might want to call a different
       version of install() for one platform than you call on another.

       The SpecMultiMethod class implements a callable object that
       handles method dispatch.  When it is called, it looks through
       registered methods and their associated specs, and it tries
       to find one that matches the package's spec.  If it finds one
       (and only one), it will call that method.

       The package author is responsible for ensuring that only one
       condition on multi-methods ever evaluates to true.  If
       multiple methods evaluate to true, this will raise an
       exception.

       This is intended for use with decorators (see below).  The
       decorator (see docs below) creates SpecMultiMethods and
       registers method versions with them.

       To register a method, you can do something like this:
           mf = SpecMultiMethod()
           mf.register("^chaos_5_x86_64_ib", some_method)

       The object registered needs to be a Spec or some string that
       will parse to be a valid spec.

       When the pmf is actually called, it selects a version of the
       method to call based on the sys_type of the object it is
       called on.

       See the docs for decorators below for more details.
    """
    def __init__(self, default=None):
        self.method_map = {}
        self.default = default
        if default:
            functools.update_wrapper(self, default)


    def register(self, spec, method):
        """Register a version of a method for a particular sys_type."""
        self.method_map[spec] = method

        if not hasattr(self, '__name__'):
            functools.update_wrapper(self, method)
        else:
            assert(self.__name__ == method.__name__)


    def __get__(self, obj, objtype):
        """This makes __call__ support instance methods."""
        return functools.partial(self.__call__, obj)


    def __call__(self, package_self, *args, **kwargs):
        """Try to find a method that matches package_self.sys_type.
           If none is found, call the default method that this was
           initialized with.  If there is no default, raise an error.
        """
        spec = package_self.spec
        matching_specs = [s for s in self.method_map if s.satisfies(spec)]
        num_matches = len(matching_specs)
        if num_matches == 0:
            if self.default is None:
                raise NoSuchMethodError(type(package_self), self.__name__,
                                        spec, self.method_map.keys())
            else:
                method = self.default

        elif num_matches == 1:
            method = self.method_map[matching_specs[0]]

        else:
            raise AmbiguousMethodError(type(package_self), self.__name__,
                                              spec, matching_specs)

        return method(package_self, *args, **kwargs)


    def __str__(self):
        return "SpecMultiMethod {\n\tdefault: %s,\n\tspecs: %s\n}" % (
            self.default, self.method_map)


class when(object):
    """This annotation lets packages declare multiple versions of
       methods like install() that depend on the package's spec.
       For example:

       .. code-block::

          class SomePackage(Package):
              ...

              def install(self, prefix):
                  # Do default install

              @when('=chaos_5_x86_64_ib')
              def install(self, prefix):
                  # This will be executed instead of the default install if
                  # the package's sys_type() is chaos_5_x86_64_ib.

              @when('=bgqos_0")
              def install(self, prefix):
                  # This will be executed if the package's sys_type is bgqos_0

       This allows each package to have a default version of install() AND
       specialized versions for particular platforms.  The version that is
       called depends on the sys_type of SomePackage.

       Note that this works for methods other than install, as well.  So,
       if you only have part of the install that is platform specific, you
       could do this:

       class SomePackage(Package):
           ...
           # virtual dependence on MPI.
           # could resolve to mpich, mpich2, OpenMPI
           depends_on('mpi')

           def setup(self):
               # do nothing in the default case
               pass

           @when('^openmpi')
           def setup(self):
               # do something special when this is built with OpenMPI for
               # its MPI implementations.


           def install(self, prefix):
               # Do common install stuff
               self.setup()
               # Do more common install stuff

       There must be one (and only one) @when clause that matches the
       package's spec.  If there is more than one, or if none match,
       then the method will raise an exception when it's called.

       Note that the default version of decorated methods must
       *always* come first.  Otherwise it will override all of the
       platform-specific versions.  There's not much we can do to get
       around this because of the way decorators work.
    """
class when(object):
    def __init__(self, spec):
        pkg = get_calling_package_name()
        self.spec = parse_anonymous_spec(spec, pkg)

    def __call__(self, method):
        # Get the first definition of the method in the calling scope
        original_method = caller_locals().get(method.__name__)

        # Create a multimethod out of the original method if it
        # isn't one already.
        if not type(original_method) == SpecMultiMethod:
            original_method = SpecMultiMethod(original_method)

        original_method.register(self.spec, method)
        return original_method


class MultiMethodError(spack.error.SpackError):
    """Superclass for multimethod dispatch errors"""
    def __init__(self, message):
        super(MultiMethodError, self).__init__(message)


class NoSuchMethodError(spack.error.SpackError):
    """Raised when we can't find a version of a multi-method."""
    def __init__(self, cls, method_name, spec, possible_specs):
        super(NoSuchMethodError, self).__init__(
            "Package %s does not support %s called with %s.  Options are: %s"
            % (cls.__name__, method_name, spec,
               ", ".join(str(s) for s in possible_specs)))


class AmbiguousMethodError(spack.error.SpackError):
    """Raised when we can't find a version of a multi-method."""
    def __init__(self, cls, method_name, spec, matching_specs):
        super(AmbiguousMethodError, self).__init__(
            "Package %s has multiple versions of %s that match %s: %s"
            % (cls.__name__, method_name, spec,
               ",".join(str(s) for s in matching_specs)))
