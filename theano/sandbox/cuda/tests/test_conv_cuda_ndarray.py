import sys, time
import numpy
# Skip test if cuda_ndarray is not available.
from nose.plugins.skip import SkipTest
import theano.sandbox.cuda as cuda_ndarray
if cuda_ndarray.enable_cuda == False:
    raise SkipTest('Optional package cuda disabled')
    
def py_conv_valid_numpy(img, kern):
    assert img.shape[1] == kern.shape[1]
    outshp = (img.shape[0], kern.shape[0], 
            img.shape[2] - kern.shape[2] + 1,
            img.shape[3] - kern.shape[3] + 1)
    out = numpy.zeros(outshp, dtype='float32')
    for b in xrange(out.shape[0]):
        for k in xrange(out.shape[1]):
            for rr in xrange(out.shape[2]):
                for cc in xrange(out.shape[3]):
                    #rr, cc is the upper-left corner of img patches
                    imgpatch = img[b,:,rr:rr+kern.shape[2], cc:cc+kern.shape[3]]
                    #print img.shape, kern.shape, imgpatch.shape, rr+kern.shape[2]-1, rr-1, -1
                    innerprod = (imgpatch[:,::-1,::-1] * kern[k,:,:,:]).sum()
                    out[b, k, rr, cc] = innerprod
    return out
def py_conv_full_numpy(img, kern):
    # manually pad the img with zeros all around, and then run it through py_conv_valid
    pad_rows = 2*(kern.shape[2]-1) + img.shape[2]
    pad_cols = 2*(kern.shape[3]-1) + img.shape[3]
    padded_img = numpy.zeros((img.shape[0], img.shape[1], pad_rows, pad_cols), dtype=img.dtype)
    padded_img[:,:,kern.shape[2]-1:kern.shape[2]-1+img.shape[2],kern.shape[3]-1:kern.shape[3]-1+img.shape[3]] = img
    return py_conv_valid(padded_img, kern)
def py_conv_scipy(img, kern, mode, subsample):
    from scipy.signal import convolve2d
    assert img.shape[1] == kern.shape[1]
    if mode == 'valid':
        outshp = (img.shape[0], kern.shape[0], 
                img.shape[2] - kern.shape[2] + 1,
                img.shape[3] - kern.shape[3] + 1)
    else:
        outshp = (img.shape[0], kern.shape[0], 
                img.shape[2] + kern.shape[2] - 1,
                img.shape[3] + kern.shape[3] - 1)
    out = numpy.zeros(outshp, dtype='float32')
    for b in xrange(out.shape[0]):
        for k in xrange(out.shape[1]):
            for s in xrange(img.shape[1]):
                out[b,k,:,:] += convolve2d(img[b,s,:,:]
                        , kern[k,s,:,:]
                        , mode)
    return out[:,:,::subsample[0], ::subsample[1]]

def _params_allgood_header():
    print "ishape kshape #Mflops CPU Mflops GPU Mflops Speedup"

def _params_allgood(ishape, kshape, mode, subsample=(1,1), img_stride=(1,1), kern_stride=(1,1), version=-1, verbose=0, random=True, print_=None, id=None, rtol=1e-5, atol = 1e-8, nb_iter=0, ones=False):
    if ones:
        assert not random
        npy_img = numpy.asarray(numpy.ones(ishape), dtype='float32')
        npy_kern = -numpy.asarray(numpy.ones(kshape), dtype='float32')
    elif random:
        npy_img = numpy.asarray(numpy.random.rand(*ishape), dtype='float32')
        npy_kern = numpy.asarray(numpy.random.rand(*kshape), dtype='float32')
    else:
        npy_img = numpy.asarray(numpy.arange(numpy.prod(ishape)).reshape(ishape), dtype='float32')+1
        npy_kern = -(numpy.asarray(numpy.arange(numpy.prod(kshape)).reshape(kshape), dtype='float32')+1)

    img = cuda_ndarray.CudaNdarray(npy_img)
    kern = cuda_ndarray.CudaNdarray(npy_kern)

    #we take the stride after the transfert as we make c_contiguous data on the GPU.
    img=img[:,:,::img_stride[0],::img_stride[1]]
    kern=kern[:,:,::kern_stride[0],::kern_stride[1]]
    npy_img = npy_img[:,:,::img_stride[0],::img_stride[1]]
    npy_kern = npy_kern[:,:,::kern_stride[0],::kern_stride[1]]

    t2 = None
    rval = True
    try:
        t0 = time.time()
        cpuval = py_conv_scipy(npy_img, npy_kern, mode, subsample)
        t1 = time.time()
        gpuval = cuda_ndarray.conv(img, kern, mode, subsample=subsample,
                                   version=version, verbose=verbose)
        t2 = time.time()
        for i in range(nb_iter):
            gpuval2 = cuda_ndarray.conv(img, kern, mode, subsample=subsample,
                                        version=version, verbose=0)
            assert numpy.allclose(numpy.asarray(gpuval),numpy.asarray(gpuval2))
            assert (numpy.asarray(gpuval)==numpy.asarray(gpuval2)).all()
        gpuval = numpy.asarray(gpuval)
        if gpuval.shape != cpuval.shape:
            print >> sys.stdout, "ERROR: shape mismatch", gpuval.shape, cpuval.shape
            rval = False
        if rval:
            rval = numpy.allclose(cpuval, gpuval, rtol = rtol)
    except NotImplementedError, e:
        print >> sys.stdout, '_params_allgood Failed allclose', e
        rval = False

    if (t2 is not None):
        if mode == 'valid':
            approx_fp = cpuval.size * ishape[1] * kshape[2] * kshape[3] * 2
        else:
            approx_fp = ishape[0] * kshape[0] * kshape[1] * kshape[2] * kshape[3] * ishape[2] * ishape[3] * 2
        approx_fp /= 1e6
        cpu_mflops = approx_fp / (t1-t0)
        gpu_mflops = approx_fp / (t2-t1)
        print >> sys.stdout, '%15s'% str(ishape), '%15s'% str(kshape),
	print >> sys.stdout, '%12.5f  %7.2f %7.2f %7.1f' % (approx_fp, 
		cpu_mflops, gpu_mflops,(t1-t0)/(t2-t1))
    if not rval:
        print >> sys.stdout, 'test_'+mode+' id='+str(id)+' FAILED for ishape, kshape, mode, subsample, img_stride, kern_stride, version', ishape, kshape, mode, subsample, img_stride, kern_stride, version
        diff=cpuval-gpuval
        diffabs=numpy.absolute(diff)
        pr_diff=diffabs/numpy.absolute(cpuval)
        nb_close=(diffabs <= (atol + rtol * numpy.absolute(gpuval))).sum()
        print "max absolute diff:",diffabs.max(),"avg abs diff:",numpy.average(diffabs)
        print "median abs diff:", numpy.median(diffabs), "nb close:",nb_close, "/", diff.size
        print "max relatif diff:",pr_diff.max(), "avg rel diff:", numpy.average(pr_diff)

	print rval
    if not rval and print_!=False:
        if npy_img.shape[0]>5:
            print "img",npy_img[0]
            print "kern",npy_kern[0]
            print "gpu",gpuval[0][0]
            print "cpu",cpuval[0][0]
            print "diff",diff[0][0]
        else:
            print "img",npy_img
            print "kern",npy_kern
            print "gpu",gpuval
            print "cpu",cpuval
            print "diff",diff
                
    return rval

def exec_conv(version, shapes, verbose, random, mode, print_=None, rtol=1e-5, ones=False):
    _params_allgood_header()
    nb_failed = 0
    nb_tests = 0

    failed_version=set()
    failed_id=[]
    for ver in version:# I put -1 in case we forget to add version in the test to.
        for id,(ishape, kshape, subshape, istride, kstride) in enumerate(shapes):
            ret=False
            try:
                ret = _params_allgood(ishape, kshape, mode,
                                      subsample=subshape, img_stride=istride, kern_stride=kstride,
                                      version=ver, verbose=verbose, random=random, id=id,print_=print_,rtol=rtol,ones=ones)
            except:
                pass
            if not ret:
                failed_version.add(ver)
                failed_id.append(id)
                nb_failed+=1
            nb_tests+=1
    if nb_failed>0:
        print "nb_failed",nb_failed,"on",nb_tests, "failed_version",failed_version, "failed_id",failed_id
        assert nb_failed==0
    else:
        print 'Executed',nb_tests,'different shapes'

def get_basic_shapes():
    return [
	#basic test of image and kernel shape
	      ((1, 1, 1, 1), (1, 1, 1, 1), (1,1), (1,1), (1,1))
            , ((1, 1, 2, 2), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 3, 3), (1, 1, 2, 2), (1,1), (1,1), (1,1))
        #basic test for unsquare kernel and image
            , ((1, 1, 2, 4), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 3, 4), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 4, 3), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 4, 4), (1, 1, 3, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 4, 4), (1, 1, 2, 3), (1,1), (1,1), (1,1))]

def get_shapes(imshp=(1,1), kshp=(1,1), subsample=(1,1), img_stride=(1,1), kern_stride=(1,1)):
    """ all possible case if we one or more of stack size, batch size, nkern. We use the gived image shape, kernel shape and subsmaple shape."""
    return [  ((1, 2)+imshp, (1, 2)+kshp,subsample, img_stride, kern_stride)#stack only
            , ((3, 1)+imshp, (1, 1)+kshp,subsample, img_stride, kern_stride)#batch only
            , ((1, 1)+imshp, (2, 1)+kshp,subsample, img_stride, kern_stride)#nkern only
            , ((3, 1)+imshp, (2, 1)+kshp,subsample, img_stride, kern_stride)#batch and nkern
            , ((3, 2)+imshp, (1, 2)+kshp,subsample, img_stride, kern_stride)#batch and stack
            , ((1, 2)+imshp, (2, 2)+kshp,subsample, img_stride, kern_stride)#stack and nkern
            , ((2, 2)+imshp, (2, 2)+kshp,subsample, img_stride, kern_stride)#batch, nkern and stack
            , ((3, 2)+imshp, (4, 2)+kshp,subsample, img_stride, kern_stride)#batch, nkern and stack
            ]
def get_shapes2(scales_img=(1,1), scales_kern=(1,1), subsample=(1,1), img_stride=(1,1), kern_stride=(1,1)):
    #basic test of stack, batch and nkern paramter
    shapes =get_shapes((1*scales_img[0],1*scales_img[1]),
                       (1*scales_kern[0],1*scales_kern[1]),subsample, img_stride, kern_stride)
    #basic test of stack, batch and nkern paramter with image and kernel shape 
    shapes +=get_shapes((2*scales_img[0],2*scales_img[1]),
                        (2*scales_kern[0],2*scales_kern[1]),subsample, img_stride, kern_stride)
    #basic test of stack, batch and nkern paramter with image and kernel shape
    shapes +=get_shapes((3*scales_img[0],3*scales_img[1]),
                        (2*scales_kern[0],2*scales_kern[1]),subsample, img_stride, kern_stride)
    #basic test of stack, batch and nkern paramter with not square image.
    shapes +=get_shapes((4*scales_img[0],3*scales_img[1]),
                        (2*scales_kern[0],2*scales_kern[1]),subsample, img_stride, kern_stride)
    #basic test of stack, batch and nkern paramter with not square image.
    shapes +=get_shapes((3*scales_img[0],4*scales_img[1]),
                        (2*scales_kern[0],2*scales_kern[1]),subsample, img_stride, kern_stride)
    #basic test of stack, batch and nkern paramter with not square kernel.
    shapes +=get_shapes((4*scales_img[0],4*scales_img[1]),
                        (3*scales_kern[0],2*scales_kern[1]),subsample, img_stride, kern_stride)
    #basic test of stack, batch and nkern paramter with not square kernel.
    shapes +=get_shapes((4*scales_img[0],4*scales_img[1]),
                        (2*scales_kern[0],3*scales_kern[1]),subsample, img_stride, kern_stride)
    return shapes

def test_valid():
    #          img shape,     kern shape, subsample shape

    shapes = get_basic_shapes()
    shapes +=get_shapes2()

    #test image stride
    shapes += get_shapes2(scales_img=(2,2),img_stride=(1,2))
    shapes += get_shapes2(scales_img=(2,2),img_stride=(2,1))
    shapes += get_shapes2(scales_img=(2,2),img_stride=(2,2))
    shapes += get_shapes2(scales_img=(2,2),img_stride=(-1,-1))
    shapes += get_shapes2(scales_img=(2,2),kern_stride=(-1,-1))

    #test subsample
    shapes += get_shapes2(scales_img=(2,2),subsample=(2,2))

    shapes += [
         #other test
              ((2, 1, 2, 2), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((3, 2, 4, 4), (4, 2, 4, 4), (1,1), (1,1), (1,1))
            , ((4, 1, 10, 10), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 4, 4), (1, 1, 2, 3), (1,1), (1,1), (1,1))
            , ((4, 1, 10, 10), (1, 1, 2, 3), (1,1), (1,1), (1,1))
            , ((4, 1, 10, 10), (1, 1, 2, 10), (1,1), (1,1), (1,1))
            , ((4, 1, 20, 10), (1, 1, 2, 10), (1,1), (1,1), (1,1))
            , ((3, 2, 8, 8), (4, 2, 4, 4), (1,1), (1,1), (1,1)) #stack, nkern, bsize
            , ((3, 2, 8, 6), (4, 2, 4, 4), (1,1), (1,1), (1,1)) #stack, nkern, bsize, non-square image
            , ((3, 2, 8, 6), (4, 2, 4, 3), (1,1), (1,1), (1,1)) #stack, nkern, bsize, non-square image, non-square kern
            , ((3, 2, 8, 6), (4, 2, 4, 6), (1,1), (1,1), (1,1)) #stack, nkern, bsize ,non-square image, non-square kern, kernsize==imgsize on one dim
            , ((16, 5, 64, 64), (8, 5, 8, 8), (1,1), (1,1), (1,1)) # a big one
            , ((16, 1, 28, 28), (20, 1, 5, 5), (1,1), (1,1), (1,1)) # MNIST LeNET layer 1
            , ((20, 16, 32, 32), (1, 16, 28, 28), (1,1), (1,1), (1,1)) # layer 1 backprop to weights
            , ((60,20,28,28), (10,20,5,5), (1,1), (2,2), (1,1))#added a test case that fail from test_nnet.py.test_conv_nnet2
            , ((10,5,28,28), (10,5,5,5), (1,1), (2,2), (1,1))#test precedent but reduced that triger the error
            ]

    shapes += [ ((60,1,28,28),(20,1,5,5), (1,1), (1,1), (1,1))#test_lenet_28 1 layers
            , ((60,20,12,12),(30,20,5,5), (1,1), (1,1), (1,1))#test_lenet_28 2 layers
            , ((60,30,8,8),(20,30,5,5), (1,1), (1,1), (1,1))#test_lenet_28 bprop 1 full
            , ((20,60,12,12),(30,60,8,8), (1,1), (1,1), (1,1))#test_lenet_28 bprop 2 valid
            , ((1,60,28,28),(20,60,24,24), (1,1), (1,1), (1,1))#test_lenet_28 bprop 2 valid
            , ((10,1,64,64),(20,1,7,7), (1,1), (1,1), (1,1))#test_lenet_64 1 layers
            , ((10,20,29,29),(30,20,7,7), (1,1), (1,1), (1,1))#test_lenet_64 2 layers
            , ((10,30,23,23),(20,30,7,7), (1,1), (1,1), (1,1))#test_lenet_64 full
            , ((20,10,29,29),(30,10,23,23), (1,1), (1,1), (1,1))#test_lenet_64 bprop 1
            , ((1,10,64,64),(20,10,58,58), (1,1), (1,1), (1,1))#test_lenet_64 bprop 2
            ]

    shapes=shapes[425:426]
    # I put -1 in case we forget to add version in the test to.
    # I put -2 to test the reference version.
    version=[-2,-1,0,1,2,3,4,5,6,7,8,9,10,11,12,13]
    verbose=0
#    version=[1]
    
    random = True
    print_ = True
    ones = False
    if ones:
        random = False
    
    exec_conv(version, shapes, verbose, random, 'valid', print_=print_, ones=ones, rtol=1.1e-5)

def test_full():
    shapes = get_basic_shapes()
    shapes +=get_shapes2()
    #test image stride
    shapes += get_shapes2(scales_img=(2,2),img_stride=(1,2))
    shapes += get_shapes2(scales_img=(2,2),img_stride=(2,1))
    shapes += get_shapes2(scales_img=(2,2),img_stride=(2,2))
    shapes += get_shapes2(scales_img=(2,2),img_stride=(-1,-1))
    shapes += get_shapes2(scales_img=(2,2),kern_stride=(-1,-1))

    #test subsample
    shapes += get_shapes2(scales_img=(2,2),subsample=(2,2))

    shapes += [
        #other test
              ((2, 1, 2, 2), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((3, 2, 4, 4), (4, 2, 4, 4), (1,1), (1,1), (1,1))
            , ((4, 1, 10, 10), (1, 1, 2, 2), (1,1), (1,1), (1,1))
            , ((1, 1, 4, 4), (1, 1, 2, 3), (1,1), (1,1), (1,1))
            , ((4, 1, 10, 10), (1, 1, 2, 3), (1,1), (1,1), (1,1))
            , ((4, 1, 10, 10), (1, 1, 2, 10), (1,1), (1,1), (1,1))
            , ((4, 1, 20, 10), (1, 1, 2, 10), (1,1), (1,1), (1,1))
            , ((3, 2, 8, 8), (4, 2, 4, 4), (1,1), (1,1), (1,1)) #stack, nkern, bsize
            , ((3, 2, 8, 6), (4, 2, 4, 4), (1,1), (1,1), (1,1)) #stack, nkern, bsize, non-square image
            , ((3, 2, 8, 6), (4, 2, 4, 3), (1,1), (1,1), (1,1)) #stack, nkern, bsize, non-square image, non-square kern
            , ((3, 2, 8, 6), (4, 2, 4, 6), (1,1), (1,1), (1,1)) #stack, nkern, bsize ,non-square image, non-square kern, kernsize==imgsize on one dim
            , ((16, 5, 64, 64), (8, 5, 8, 8), (1,1), (1,1), (1,1)) # a big one
            , ((16, 1, 28, 28), (20, 1, 5, 5), (1,1), (1,1), (1,1)) # MNIST LeNET layer 1
            , ((20, 16, 32, 32), (1, 16, 28, 28), (1,1), (1,1), (1,1)) # layer 1 backprop to weights

        #other test
            , ((3, 1, 1, 1), (2, 1, 5, 3), (1,1), (1,1), (1,1))#kernel bigger then image
            , ((3, 2, 1, 1), (4, 2, 1, 1), (1,1), (1,1), (1,1))
            , ((3, 2, 4, 4), (4, 2, 2, 6), (1,1), (1,1), (1,1))
            , ((3, 2, 4, 4), (4, 2, 8, 6), (1,1), (1,1), (1,1))#kernel bigger then image
            , ((4, 2, 10, 10), (3, 2, 2, 12), (1,1), (1,1), (1,1))
            ]
    shapes += [
#        ((60,1,28,28),(20,1,5,5), (1,1), (1,1), (1,1))#test_lenet_28 1 layers
#            , ((60,20,12,12),(30,20,5,5), (1,1), (1,1), (1,1))#test_lenet_28 2 layers
             ((60,30,8,8),(20,30,5,5), (1,1), (1,1), (1,1))#test_lenet_28 bprop 1 full
#            , ((20,60,12,12),(30,60,8,8), (1,1), (1,1), (1,1))#test_lenet_28 bprop 2 valid
#            , ((1,60,28,28),(20,60,24,24), (1,1), (1,1), (1,1))#test_lenet_28 bprop 2 valid
#            , ((10,1,64,64),(20,1,7,7), (1,1), (1,1), (1,1))#test_lenet_64 1 layers
#            , ((10,20,29,29),(30,20,7,7), (1,1), (1,1), (1,1))#test_lenet_64 2 layers
            , ((10,30,23,23),(20,30,7,7), (1,1), (1,1), (1,1))#test_lenet_64 full
#            , ((20,10,29,29),(30,10,23,23), (1,1), (1,1), (1,1))#test_lenet_64 bprop 1
#            , ((1,10,64,64),(20,10,58,58), (1,1), (1,1), (1,1))#test_lenet_64 bprop 2
            ]

#    shapes=shapes[:277]
    version=[-2,-1,0,1,2,3,4,5]
    verbose=0
#    version=[4]
    random=True

    exec_conv(version, shapes, verbose, random, 'full')

def test_subsample():
    # implement when
    shapes = [ 
            ((1, 1, 1, 1), (1, 1, 1, 1), (1,1))
            , ((1, 1, 1, 1), (1, 1, 1, 1), (2,2))
            , ((4, 2, 10, 10), (3, 2, 2, 2), (1, 3))
            , ((4, 2, 10, 10), (3, 2, 2, 2), (3, 3))
            , ((4, 2, 10, 10), (3, 2, 2, 2), (3, 1))
            ]
    all_good = True

    _params_allgood_header()
    for ishape, kshape, ds in shapes:
        if not _params_allgood(ishape, kshape, 'full', subsample=ds):
            all_good = False
        if not _params_allgood(ishape, kshape, 'valid', subsample=ds):
            all_good = False
    assert all_good

def test_logical_shapes():
    # implement when
    print >> sys.stderr, "INFO: test_logical_shapes not implemented (i.e. imshp_logical, kshp_logical, kshp_logical_top_aligned)"


def _test_dummy():
    ishape = (1, 1, 5, 5)
    kshape = (1, 1, 3, 3)
    mode = 'valid'
    subsample = (1,1)

    npy_img = numpy.asarray(numpy.random.rand(*ishape), dtype='float32')
    npy_kern = numpy.asarray(numpy.random.rand(*kshape), dtype='float32')

    img = cuda_ndarray.CudaNdarray(npy_img)
    kern = cuda_ndarray.CudaNdarray(npy_kern)

    #print >> sys.stdout, '_params_allgood trying ', ishape, kshape, mode
    t2 = None
    rval = True

    t0 = time.time()
    cpuval = py_conv_scipy(npy_img, npy_kern, mode, subsample)
    t1 = time.time()
    gpuval = cuda_ndarray.conv(img, kern, mode, subsample)
    t2 = time.time()
    gpuval = numpy.asarray(gpuval)
    print gpuval
    print cpuval


def benchmark():
    
    shapes_valid = [
        #test_lenet_28 shape
        ((20, 60,12,12), (30,60,8,8), (1,1), (1,1), (1,1))#valid
        ,((60, 20,12,12), (30,20,5,5), (1,1), (1,1), (1,1))#valid
        ,((60, 1,28,28), (20,1,5,5), (1,1), (1,1), (1,1))#valid
        ,((1, 60,28,28), (20,60,24,24), (1,1), (1,1), (1,1))#valid
        #test_lenet_32 shape
        ,((20, 60,14,14), (30,60,10,10), (1,1), (1,1), (1,1))#valid
        ,((60, 20,14,14), (30,20,5,5), (1,1), (1,1), (1,1))#valid
        ,((60, 1,32,32), (20,1,5,5), (1,1), (1,1), (1,1))#valid
        ,((1, 60,32,32), (20,60,28,28), (1,1), (1,1), (1,1))#valid
        #test_lenet_64 shape
        ,((10, 20,29,29), (30,20,7,7), (1,1), (1,1), (1,1))#valid
        ,((20, 10,29,29), (30,10,23,23), (1,1), (1,1), (1,1))#valid
        ,((10, 1,64,64), (20,1,7,7), (1,1), (1,1), (1,1))#valid
        ,((1, 10,64,64), (20,10,58,58), (1,1), (1,1), (1,1))#valid
        #test_lenet_108 shape
        ,((10, 20,51,51), (30,20,7,7), (1,1), (1,1), (1,1))#valid
        ,((20, 10,51,51), (30,10,45,45), (1,1), (1,1), (1,1))#valid
        ,((10, 1,108,108), (20,1,7,7), (1,1), (1,1), (1,1))#valid
        ,((1, 10,108,108), (20,10,102,102), (1,1), (1,1), (1,1))#valid
        #test_lenet_256 shape
        ,((2, 20,124,124), (30,20,9,9), (1,1), (1,1), (1,1))#valid 
        ,((20, 2,124,124), (30,2,116,116), (1,1), (1,1), (1,1))#valid
        ,((2, 1,256,256), (20,1,9,9), (1,1), (1,1), (1,1))#valid
        ,((1, 2,256,256), (20,2,248,248), (1,1), (1,1), (1,1))#valid
            ]

    shapes_full = [
        #test_lenet_28 shape
         ((60, 30,8,8), (20, 30, 5, 5), (1,1), (1,1), (1,1))#full
        #test_lenet_32 shape
         ,((60, 30,10,10), (20, 30, 5, 5), (1,1), (1,1), (1,1))#full conv_full_patch_stack_padded' N=1
        #test_lenet_64 shape
         ,((10, 30,23,23), (20, 30, 7, 7), (1,1), (1,1), (1,1))#full conv_full_patch_stack_padded' N=3
        #test_lenet_108 shape
         ,((10, 30,45,45), (20, 30, 7, 7), (1,1), (1,1), (1,1))#full 'conv_full_patch_stack_padded' N=9
        #test_lenet_256 shape
         ,((2, 30,116,116), (20, 30, 9,9), (1,1), (1,1), (1,1))#full conv_reference_full
            ]


#    shapes_valid=shapes_valid[-1:]
#    shapes_full=shapes_full[-1:]
    version=[-1]
    verbose=1
    random=True

    exec_conv(version, shapes_valid, verbose, random, 'valid', print_=None, rtol=1e-3)
    exec_conv(version, shapes_full, verbose, random, 'full')
