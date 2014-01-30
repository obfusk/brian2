'''
Module providing the base `CodeObject` and related functions.
'''
import copy
import functools
import weakref

from brian2.core.functions import Function
from brian2.core.names import Nameable
from brian2.equations.unitcheck import check_units_statements
from brian2.utils.logger import get_logger

from .translation import analyse_identifiers

__all__ = ['CodeObject',
           'CodeObjectUpdater',
           ]

logger = get_logger(__name__)


class CodeObject(Nameable):
    '''
    Executable code object.
    
    The ``code`` can either be a string or a
    `brian2.codegen.templates.MultiTemplate`.
    
    After initialisation, the code is compiled with the given namespace
    using ``code.compile(namespace)``.
    
    Calling ``code(key1=val1, key2=val2)`` executes the code with the given
    variables inserted into the namespace.
    '''
    
    #: The `Language` used by this `CodeObject`
    language = None
    #: A short name for this type of `CodeObject`
    class_name = None

    def __init__(self, owner, code, variables, name='codeobject*'):
        Nameable.__init__(self, name=name)
        try:    
            owner = weakref.proxy(owner)
        except TypeError:
            pass # if owner was already a weakproxy then this will be the error raised
        self.owner = owner
        self.code = code
        self.variables = variables

    def update_namespace(self):
        '''
        Update the namespace for this timestep. Should only deal with variables
        where *the reference* changes every timestep, i.e. where the current
        reference in `namespace` is not correct.
        '''
        pass

    def get_compile_methods(self, variables):
        meths = []
        for var, var in variables.items():
            if isinstance(var, Function):
                meths.append(functools.partial(var.on_compile,
                                               language=self.language,
                                               var=var))
        return meths

    def compile(self):
        pass

    def __call__(self, **kwds):
        self.update_namespace()
        self.namespace.update(**kwds)

        return self.run()

    def run(self):
        '''
        Runs the code in the namespace.
        
        Returns
        -------
        return_value : dict
            A dictionary with the keys corresponding to the `output_variables`
            defined during the call of `Language.code_object`.
        '''
        raise NotImplementedError()


def check_code_units(code, group, additional_variables=None,
                     additional_namespace=None,
                     level=0):
    '''
    Check statements for correct units.

    Parameters
    ----------
    code : str
        The series of statements to check
    group : `Group`
        The context for the code execution
    additional_variables : dict-like, optional
        A mapping of names to `Variable` objects, used in addition to the
        variables saved in `self.group`.
    additional_namespace : dict-like, optional
        An additional namespace, as provided to `Group.before_run`
    level : int, optional
        How far to go up in the stack to find the calling frame.

    Raises
    ------
    DimensionMismatchError
        If `code` has unit mismatches
    '''
    all_variables = dict(group.variables)
    if additional_variables is not None:
        all_variables.update(additional_variables)

    # Resolve the namespace, resulting in a dictionary containing only the
    # external variables that are needed by the code -- keep the units for
    # the unit checks
    # Note that here we do not need to recursively descend into
    # subexpressions. For unit checking, we only need to know the units of
    # the subexpressions not what variables they refer to
    _, _, unknown = analyse_identifiers(code, all_variables)
    try:
        resolved_namespace = group.resolve_all(unknown,
                                               run_namespace=additional_namespace,
                                               level=level+1)
    except KeyError as ex:
        raise KeyError('Error occured when checking "%s": %s' % (code,
                                                                 str(ex)))

    all_variables.update(resolved_namespace)

    check_units_statements(code, all_variables)


def create_runner_codeobj(group, code, template_name,
                          variable_indices=None,
                          name=None, check_units=True,
                          needed_variables=None,
                          additional_variables=None,
                          additional_namespace=None,
                          level=0,
                          template_kwds=None,):
    ''' Create a `CodeObject` for the execution of code in the context of a
    `Group`.

    Parameters
    ----------
    group : `Group`
        The group where the code is to be run
    code : str
        The code to be executed.
    template_name : str
        The name of the template to use for the code.
    variable_indices : dict-like, optional
        A mapping from `Variable` objects to index names (strings).  If none is
        given, uses the corresponding attribute of `group`.
    name : str, optional
        A name for this code object, will use ``group + '_codeobject*'`` if
        none is given.
    check_units : bool, optional
        Whether to check units in the statement. Defaults to ``True``.
    needed_variables: list of str, optional
        A list of variables that are neither present in the abstract code, nor
        in the ``USES_VARIABLES`` statement in the template. This is only
        rarely necessary, an example being a `StateMonitor` where the
        names of the variables are neither known to the template nor included
        in the abstract code statements.
    additional_variables : dict-like, optional
        A mapping of names to `Variable` objects, used in addition to the
        variables saved in `group`.
    additional_namespace : dict-like, optional
        A mapping from names to objects, used in addition to the namespace
        saved in `group`.
    level : int, optional
        How far to go up in the stack to find the call frame.
    template_kwds : dict, optional
        A dictionary of additional information that is passed to the template.
    '''
    logger.debug('Creating code object for abstract code:\n' + str(code))
    from brian2.devices import get_device
    device = get_device()

    if check_units:
        if isinstance(code, dict):
            for c in code.values():
                check_code_units(c, group,
                                 additional_variables=additional_variables,
                                 additional_namespace=additional_namespace,
                                 level=level+1)
        else:
            check_code_units(code, group,
                             additional_variables=additional_variables,
                             additional_namespace=additional_namespace,
                             level=level+1)

    codeobj_class = device.code_object_class(group.codeobj_class)
    template = getattr(codeobj_class.templater, template_name)

    all_variables = dict(group.variables)
    if additional_variables is not None:
        all_variables.update(additional_variables)

    # Determine the identifiers that were used
    if isinstance(code, dict):
        used_known = set()
        unknown = set()
        for v in code.values():
            _, uk, u = analyse_identifiers(v, all_variables, recursive=True)
            used_known |= uk
            unknown |= u
    else:
        _, used_known, unknown = analyse_identifiers(code, all_variables,
                                                     recursive=True)

    logger.debug('Unknown identifiers in the abstract code: ' + str(unknown))

    # Only pass the variables that are actually used
    variables = group.resolve_all(used_known | unknown,
                                  additional_variables=additional_variables,
                                  run_namespace=additional_namespace,
                                  level=level+1)

    # Add variables that are not in the abstract code, nor specified in the
    # template but nevertheless necessary
    if needed_variables is None:
        needed_variables = []
    variables.update(group.resolve_all(needed_variables,
                                       additional_variables=additional_variables,
                                       run_namespace=additional_namespace,
                                       level=level+1))

    # Also add the variables that the template needs
    variables.update(group.resolve_all(template.variables,
                                       additional_variables=additional_variables,
                                       run_namespace=additional_namespace,
                                       level=level+1))

    # always add N, the number of neurons or synapses
    variables['N'] = all_variables['N']

    if name is None:
        if group is not None:
            name = '%s_%s_codeobject*' % (group.name, template_name)
        else:
            name = '%s_codeobject*' % template_name

    all_variable_indices = copy.copy(group.variables.indices)
    if additional_variables is not None:
        all_variable_indices.update(additional_variables.indices)
    if variable_indices is not None:
        all_variable_indices.update(variable_indices)

    # Add the indices needed by the variables
    varnames = variables.keys()
    for varname in varnames:
        var_index = all_variable_indices[varname]
        if var_index != '_idx':
            variables[var_index] = all_variables[var_index]

    return device.code_object(owner=group,
                              name=name,
                              abstract_code=code,
                              variables=variables,
                              template_name=template_name,
                              variable_indices=all_variable_indices,
                              template_kwds=template_kwds,
                              codeobj_class=group.codeobj_class)
