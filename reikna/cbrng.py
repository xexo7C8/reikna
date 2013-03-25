import time
import numpy

from reikna.helpers import *
from reikna.core import *
import reikna.cluda.dtypes as dtypes


TEMPLATE = template_for(__file__)


def create_counters(ctx, size, rng, distribution, rng_params):
    """
    Create a counter array on a device for use in :py:class:`~reikna.cbrng.CBRNG`.

    :param ctx: a context object.
    :param size: a shape of random numbers array.
    :param rng: random number generator name.
    :param distribution: random distribution name.
    :param rng_params: random number generator parameters.
    """
    size = wrap_in_tuple(size)
    return ctx.to_device(numpy.zeros(
        size + (rng_params['words'],),
        numpy.uint32 if rng_params['bitness'] == 32 else numpy.uint64))


def create_key(rng, rng_params, seed=None):
    full_key = numpy.zeros(
        rng_params['words'] // (2 if rng == 'philox' else 1),
        numpy.uint32 if rng_params['bitness'] == 32 else numpy.uint64)

    bitness = rng_params['bitness']
    if bitness == 32:
        key_words = full_key.size - 1
    else:
        if full_key.size > 1:
            key_words = (full_key.size - 1) * 2
        else:
            # Philox-2x64 case, key is a single 64-bit integer.
            # We use first 32 bit for the key, and the remaining 32 bit for a thread identifier.
            key_words = 1

    if isinstance(seed, numpy.ndarray):
        # explicit key was provided
        assert seed.size == key_words and seed.dtype == numpy.uint32
        key = seed.flatten()
    else:
        # use numpy to generate the key from seed
        np_rng = numpy.random.RandomState(seed)

        # 32-bit Python can only generate random integer up to 2**31-1
        key = np_rng.randint(0, 2**16, key_words * 2)

    subwords = bitness // 16
    for i, x in enumerate(key):
        full_key[i // subwords] += x << (16 * (subwords - 1 - i % subwords))

    return full_key


class CBRNG(Computation):
    """
    Counter-based pseudo-random number generator.
    Based on the paper by Salmon et al.,
    `P. Int. C. High. Perform. 16 (2011) <http://dx.doi.org/doi:10.1145/2063384.2063405>`_.
    and the source code of `Random123 library <http://www.thesalmons.org/john/random123/>`_.

    .. py:method:: prepare_for(new_counters, randoms, old_counters, \\
        seed=None, \\
        rng='philox', rng_params=None, \\
        distribution='uniform_float', distribution_params=None)

        :param new_counters: array of updated counters.
        :param randoms: array with generated random numbers.
        :param old_counters: array of initial counters,
            generated by :py:func:`~reikna.cbrng.create_counters`.
        :param seed: ``None`` for random seed, or an integer.
        :param rng: ``"philox"`` or ``"threefry"``.
        :param rng_params: a dictionary with ``bitness`` (32 or 64,
            corresponds to the size of generated random integers),
            ``words`` (2 or 4, number of integers generated in one go),
            and ``rounds`` (the more rounds, the better randomness is achieved;
            default values are big enough to qualify as PRNG).
        :param distribution: ``uniform_integer``, ``uniform_float``,
            ``normal_bm`` (normal distribution using Box-Muller transform),
            or ``gamma``.
        :param distribution_params: a dictionary with distribution-specific parameters.

    .. note::
        Philox does not support ``words=2``, ``bitness=32`` mode,
        because it makes the key too small.
        The period of the generator is ``2 ** (bitness * words)``.
        Key is assembled from the thread id (``bitness`` bits, or 32 bits for ``philox-2x64``),
        plus the part idential for all threads (derived from ``seed``).
    """

    def _get_argnames(self):
        return ('new_counters', 'randoms'), ('old_counters',), tuple()

    def _get_basis_for(self, new_counters, randoms, old_counters,
            seed=None,
            rng='philox', rng_params=None,
            distribution='int32', distribution_params=None):

        assert rng in ('philox', 'threefry')

        assert new_counters.dtype == old_counters.dtype
        assert new_counters.shape == old_counters.shape
        assert randoms.shape[-len(new_counters.shape)+1:] == new_counters.shape[:-1]

        bs = AttrDict()
        bs.dtype = dtypes.normalize_type(randoms.dtype)
        bs.shape = new_counters.shape[:-1]
        bs.size = product(bs.shape)
        bs.batch = product(randoms.shape[:-len(bs.shape)])
        bs.rng = rng
        bs.distribution = distribution

        default_rounds = dict(philox=10, threefry=20)[rng]
        rng_params_default = AttrDict(bitness=64, words=4, rounds=default_rounds)
        if rng_params is not None:
            rng_params_default.update(rng_params)
        bs.rng_params = rng_params_default
        bs.rng_params.key = create_key(bs.rng, bs.rng_params, seed=seed)

        distribution_params_default = dict(
            uniform_integer=AttrDict(min=0, max=2**bs.rng_params.bitness-1),
            uniform_float=AttrDict(min=0, max=1),
            normal_bm=AttrDict(mean=0, std=1),
            gamma=AttrDict(shape=1, scale=1))
        distribution_params_default = distribution_params_default[distribution]
        if distribution_params is not None:
            distribution_params_default.update(distribution_params)
        bs.distribution_params = distribution_params_default

        return bs

    def _get_argvalues(self, basis):
        return dict(
            new_counters=ArrayValue(basis.shape, basis.dtype),
            old_counters=ArrayValue(basis.shape, basis.dtype),
            randoms=ArrayValue((basis.batch,) + basis.shape, basis.dtype))

    def _construct_operations(self, basis, device_params):

        operations = self._get_operation_recorder()

        operations.add_kernel(
            TEMPLATE, 'cbrng', ['new_counters', 'randoms', 'old_counters'],
            global_size=product(basis.shape),
            dependencies=[('new_counters', 'old_counters'), ('new_counters', 'randoms')])

        return operations
