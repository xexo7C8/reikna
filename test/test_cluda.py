import pytest

import tigger.cluda as cluda
import tigger.cluda.dtypes as dtypes
from helpers import *


TEST_DTYPES = [
	numpy.int8, numpy.int16, numpy.int32,
	numpy.float32, numpy.float64,
	numpy.complex64, numpy.complex128
]


def simple_context_test(ctx):
    shape = (1000,)
    dtype = numpy.float32

    a = get_test_array(shape, dtype)
    a_dev = ctx.to_device(a)
    a_back = ctx.from_device(a_dev)

    assert diff_is_negligible(a, a_back)

def test_create_new_context(cluda_api):
	ctx = cluda_api.Context.create()
	simple_context_test(ctx)
	ctx.release()

def test_connect_to_context(cluda_api):
	ctx = cluda_api.Context.create()

	ctx2 = cluda_api.Context(ctx.context)
	ctx3 = cluda_api.Context(ctx.context, async=False)

	simple_context_test(ctx)
	simple_context_test(ctx2)
	simple_context_test(ctx3)

	ctx3.release()
	ctx2.release()

	ctx.release()

def test_connect_to_context_and_stream(cluda_api):
	ctx = cluda_api.Context.create()
	stream = ctx.create_stream()

	ctx2 = cluda_api.Context(ctx.context, stream=stream)
	ctx3 = cluda_api.Context(ctx.context, stream=stream, async=False)

	simple_context_test(ctx)
	simple_context_test(ctx2)
	simple_context_test(ctx3)

	ctx3.release()
	ctx2.release()

	ctx.release()

@pytest.mark.parametrize(
	"dtype", TEST_DTYPES,
	ids=[dtypes.normalize_type(dtype).name for dtype in TEST_DTYPES])
def test_dtype_support(ctx, dtype):
	# Test passes if either context correctly reports that it does not support given dtype,
	# or it successfully compiles kernel that operates with this dtype.

	N = 256

	if not ctx.supports_dtype(dtype):
		return

	module = ctx.compile(
	"""
	KERNEL void test(
		GLOBAL_MEM ${ctype} *dest, GLOBAL_MEM ${ctype} *a, GLOBAL_MEM ${ctype} *b)
	{
	  const int i = LID_0;
	  ${ctype} temp = ${func.mul(dtype, dtype)}(a[i], b[i]);
	  dest[i] = ${func.div(dtype, dtype)}(temp, b[i]);
	}
	""", ctype=dtypes.ctype(dtype), dtype=dtype)

	test = module.test

	# we need results to fit even in unsigned char
	a = get_test_array(N, dtype, high=8)
	b = get_test_array(N, dtype, no_zeros=True, high=8)

	a_dev = ctx.to_device(a)
	b_dev = ctx.to_device(b)
	dest_dev = ctx.empty_like(a_dev)
	test(dest_dev, a_dev, b_dev, block=(N,1,1), grid=(1,1))
	assert diff_is_negligible(ctx.from_device(dest_dev), a)
