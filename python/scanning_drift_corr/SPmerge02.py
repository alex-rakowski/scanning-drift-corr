"""The file contains the SPmerge02 function
"""

import warnings

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import convolve
from scipy.ndimage.morphology import binary_dilation

from scanning_drift_corr.SPmakeImage import SPmakeImage
from scanning_drift_corr.SPmerge02_initial import SPmerge02_initial
from scanning_drift_corr.tools import distance_transform

def SPmerge02(sm, refineMaxSteps=None, initialRefineSteps=None, **kwargs):
    """Refinement function for scanning probe image

    Parameters
    ----------
    sm : sMerge object
        the sMerge object contains all the data.
    refineMaxSteps : int, optional
        maximum number of refinement steps. Default to None, set to 32.
    initialRefineSteps : int, optional
        number of initial alignment steps. Default to None, set to 8 if it has
        not been performed, or set to 0 if it has been performed


    densityCutoff : float, optional
        density cutoff for image boundaries (norm. to 1). Default to 0.8.
    distStart : float, optional
        radius of # of scanlines used for initial alignment. Default to
        mean of raw data divided by 16.
    initialShiftMaximum : float, optional
        maximum number of pixels shifted per line for the initial alignment
        step. This value should have a maximum of 1, but can be set lower
        to stabilize initial alignment. Default to 0.25.
    originInitialAverage : float, optional
        window sigma in px for initial smoothing. Default to mean of raw data
        divided by 16.


    refineInitialStep : float, optional
        initial step size for final refinement, in pixels. Default to 0.5.
    pixelsMovedThreshold : float, optional
        if number of pixels shifted (per image) is below this value,
        refinement will be halted. Default to 0.1.
    stepSizeReduce : float, optional
        when a scanline origin does not move, step size will be reduced by
        this factor. Default to 0.5.
    flagPointOrder : bool, optional
        use this flag to force origins to be ordered, i.e. disallow points
        from changing their order. Default to True.
    originWindowAverage : float, optional
        window sigma in px for smoothing scanline origins. Set this value to
        zero to not use window avg. This window is relative to linear steps.
        Default to 1.


    flagGlobalShift : bool, optional
        if this flag is true, a global phase correlation, performed each
        final iteration (This is meant to fix unit cell shifts and similar
        artifacts). This option is highly recommended! Default to False.
    flagGlobalShiftIncrease : bool, optional
        if this option is true, the global scoring function is allowed to
        increase after global phase correlation step. (false is more stable)
        Default to False.
    minGlobalShift : float, optional
        global shifts only if shifts > this value (pixels). Default to 1.
    densityDist : float, optional
         density mask edge threshold. To generate a moving average along the
         scanline origins (make scanline steps more linear). Default to mean
         of raw data divided by 32.


    flagRemakeImage : bool, optional
        whether to recompute the image. Default to True.
    flagReportProgress : bool, optional
        whether to show progress bars or not. Default to True.
    flagPlot : bool
        to plot the aligned images of not. Default to True.


    """

    # ignore unknown input arguments
    _args_list = ['densityCutoff', 'distStart', 'initialShiftMaximum',
                  'originInitialAverage', 'refineInitialStep',
                  'pixelsMovedThreshold', 'stepSizeReduce', 'flagPointOrder',
                  'originWindowAverage', 'flagGlobalShift',
                  'flagGlobalShiftIncrease', 'minGlobalShift', 'densityDist',
                  'flagRemakeImage', 'flagPlot', 'flagReportProgress']
    for key in kwargs.keys():
        if key not in _args_list:
            msg = "The argument '{}' is not recognised, and it is ignored."
            warnings.warn(msg.format(key), RuntimeWarning)

    # if number of final alignment not provided, set to 32
    if refineMaxSteps is None:
        refineMaxSteps = 32

    # if number of initial alignment not provided, set to 8 if the scanActive
    # attribute is None (i.e. no initial alignment has been performed, so do
    # it), else skip initial alignment
    if initialRefineSteps is None:
        if sm.scanActive is None:
            initialRefineSteps = 8
        else:
            initialRefineSteps = 0


    # set default values or from input arguments
    meanScanLines = np.mean(sm.scanLines.shape[1:])
    # for initial alignment
    densityCutoff = kwargs.get('densityCutoff', 0.8)
    distStart = kwargs.get('distStart', meanScanLines/16)
    initialShiftMaximum = kwargs.get('initialShiftMaximum', 1/4)
    originInitialAverage = kwargs.get('originInitialAverage', meanScanLines/16)
    # for final alignment
    refineInitialStep = kwargs.get('refineInitialStep', 1/2)
    pixelsMovedThreshold = kwargs.get('pixelsMovedThreshold', 0.1)
    stepSizeReduce = kwargs.get('stepSizeReduce', 1/2)
    flagPointOrder = kwargs.get('flagPointOrder', True)
    originWindowAverage = kwargs.get('originWindowAverage', 1)
    # for global phase correlation
    flagGlobalShift = kwargs.get('flagGlobalShift', False)
    flagGlobalShiftIncrease = kwargs.get('flagGlobalShiftIncrease', False)
    minGlobalShift = kwargs.get('minGlobalShift', 1)
    densityDist = kwargs.get('densityDist', meanScanLines/32)
    # general use
    flagRemakeImage = kwargs.get('flagRemakeImage', True)
    flagPlot = kwargs.get('flagPlot', True)
    flagReportProgress = kwargs.get('flagReportProgress', True)


    # if required, perform initial alignment
    if initialRefineSteps > 0:
        print('Initial refinement ...')

        #TODO add progress bar tqdm later
        for _ in range(initialRefineSteps):
            SPmerge02_initial(sm, densityCutoff=densityCutoff,
                              distStart=distStart,
                              initialShiftMaximum=initialShiftMaximum)

            # If required, compute moving average of origins using KDE.
            if originInitialAverage > 0:
                _kernel_on_origin(sm, originInitialAverage)



    # ==================================
    # split here when refactoring
    # ==================================

    # Main alignment steps
    print('Beginning primary refinement ...')


    scanOrStep = np.ones((sm.numImages, sm.nr)) * refineInitialStep
    dxy = np.array([[0,1,-1,0,0], [0,0,0,1,-1]])
    alignStep = 1
    sm.stats = np.zeros((refineMaxSteps+1, 2))


    while alignStep <= refineMaxSteps:
        # Reset pixels moved count
        pixelsMoved = 0

        # Compute all images from current origins
        for k in range(sm.numImages):
            # sMerge changed!
            sm = SPmakeImage(sm, k)

        # Get mean absolute difference as a fraction of the mean scanline intensity.
        imgT_mean = sm.imageTransform.mean(axis=0)
        Idiff = np.abs(sm.imageTransform - imgT_mean).mean(axis=0)
        dmask = sm.imageDensity.min(axis=0) > densityCutoff
        img_mean = np.abs(sm.scanLines).mean()
        meanAbsDiff = Idiff[dmask].mean() / img_mean
        sm.stats[alignStep-1, :] = np.array([alignStep-1, meanAbsDiff])

        # If required, check for global alignment of images
        if flagGlobalShift:
            print('Checking global alignment ...')
            _global_phase_correlation(sm, scanOrStep, meanAbsDiff, densityCutoff,
                                      densityDist,
                                      flagGlobalShiftIncrease,
                                      minGlobalShift, refineInitialStep, alignStep)

        # Refine each image in turn, against the sum of all other images
        for k in range(sm.numImages):
            # Generate alignment image, mean of all other scanline datasets,
            # unless user has specified a reference image.
            if sm.imageRef is None:
                indsAlign = np.arange(sm.numImages, dtype=int)
                indsAlign = indsAlign[indsAlign != k]

                dens_cut = sm.imageDensity[indsAlign, ...] > densityCutoff
                imageAlign = (sm.imageTransform[indsAlign, ...] * dens_cut).sum(axis=0)
                dens = dens_cut.sum(axis=0)
                sub = dens > 0
                imageAlign[sub] = imageAlign[sub] / dens[sub]
                imageAlign[~sub] = np.mean(imageAlign[sub])
            else:
                imageAlign = sm.imageRef


            # If ordering is used as a condition, determine parametric positions
            if flagPointOrder:
                # Use vector perpendicular to scan direction (negative 90 deg)
                nn = np.array([sm.scanDir[k, 1], -sm.scanDir[k, 0]])
                vParam = nn[0]*sm.scanOr[k, 0, :] + nn[1]*sm.scanOr[k, 1, :]

            # Loop through each scanline and perform alignment
            for m in range(sm.nr):
                # Refine score by moving the origin of this scanline
                orTest = sm.scanOr[k, :, m][:, None] + dxy*scanOrStep[k, m]

                # If required, force ordering of points
                if flagPointOrder:
                    vTest = nn[0]*orTest[0, :] + nn[1]*orTest[1, :]

                    if m == 0:
                        # no lower bound?
                        vBound = np.array([-np.inf, vParam[m+1]])
                    elif m == sm.nr-1:
                        # no upper bound?
                        vBound = np.array([vParam[m-1], np.inf])
                    else:
                        vBound = np.array([vParam[m-1], vParam[m+1]])

                    # check out of bound entries?
                    for p in range(dxy.shape[1]):
                        if vTest[p] < vBound[0]:
                            orTest[:, p] += nn*(vBound[0]-vTest[p])
                        elif vTest[p] > vBound[1]:
                            orTest[:, p] += nn*(vBound[1]-vTest[p])

                # Loop through origin tests
                inds = np.arange(1, sm.nc+1)
                score = np.zeros(dxy.shape[1])
                for p in range(dxy.shape[1]):
                    xInd = orTest[0, p] + inds*sm.scanDir[k, 0]
                    yInd = orTest[1, p] + inds*sm.scanDir[k, 1]

                    # Prevent pixels from leaving image boundaries
                    xInd = np.clip(xInd, 0, sm.imageSize[0]-2).ravel()
                    yInd = np.clip(yInd, 0, sm.imageSize[1]-2).ravel()

                    # Bilinear coordinates
                    xF = np.floor(xInd).astype(int)
                    yF = np.floor(yInd).astype(int)
                    dx = xInd - xF
                    dy = yInd - yF

                    # scanLines indices switched
                    score[p] = calcScore(imageAlign, xF, yF, dx, dy,
                                         sm.scanLines[k, m, :])

                # Note that if moving origin does not change score, dxy = (0,0)
                # will be selected (ind = 0).
                ind = np.argmin(score)
                if ind == 0:
                    # Reduce the step size for this origin
                    scanOrStep[k, m] *= stepSizeReduce
                else:
                    pshift = np.linalg.norm(orTest[:,ind] - sm.scanOr[k, :, m])
                    pixelsMoved += pshift
                    # change sMerge!
                    sm.scanOr[k, :, m] = orTest[:,ind]

                #TODO add progress bar tqdm later



        # If required, compute moving average of origins using KDE.
        if originWindowAverage > 0:
            _kernel_on_origin(sm, originWindowAverage)

        # If pixels moved is below threshold, halt refinement
        if (pixelsMoved/sm.numImages) < pixelsMovedThreshold:
            alignStep = refineMaxSteps + 1
        else:
            alignStep += 1

    # Remake images for plotting
    if flagRemakeImage:
        print('Recomputing images and plotting ...')
        for k in range(sm.numImages):
            # sMerge changed!
            sm = SPmakeImage(sm, k)

    # Get final stats (instead of just before plotting)
    # Get mean absolute difference as a fraction of the mean scanline intensity.
    imgT_mean = sm.imageTransform.mean(axis=0)
    Idiff = np.abs(sm.imageTransform - imgT_mean).mean(axis=0)
    dmask = sm.imageDensity.min(axis=0) > densityCutoff
    img_mean = np.abs(sm.scanLines).mean()
    meanAbsDiff = Idiff[dmask].mean() / img_mean
    sm.stats[alignStep-1, :] = np.array([alignStep-1, meanAbsDiff])

    if flagPlot:
        _plot(sm)


    return sm


def calcScore(image, xF, yF, dx, dy, intMeas):

    imgsz = image.shape

    rind1 = np.ravel_multi_index((xF, yF), imgsz)
    rind2 = np.ravel_multi_index((xF+1, yF), imgsz)
    rind3 = np.ravel_multi_index((xF, yF+1), imgsz)
    rind4 = np.ravel_multi_index((xF+1, yF+1), imgsz)

    int1 = image.ravel()[rind1] * (1-dx) * (1-dy)
    int2 = image.ravel()[rind2] * dx * (1-dy)
    int3 = image.ravel()[rind3] * (1-dx) * dy
    int4 = image.ravel()[rind4] * dx * dy

    imageSample = int1 + int2 + int3 + int4

    score = np.abs(imageSample - intMeas).sum()

    return score


def _kernel_on_origin(sm, originAverage):
    # Make kernel for moving average of origins
    r = np.ceil(3*originAverage)
    v = np.arange(-r, r+1)
    KDEorigin = np.exp(-v**2/(2*originAverage**2))

    KDEnorm = 1 / convolve(np.ones(sm.scanOr.shape), KDEorigin[:, None, None].T, 'same')

    # need to offset 1 here??
    basisOr = np.vstack([np.zeros(sm.nr), np.arange(0, sm.nr)]) + 1

    scanOrLinear = np.zeros(sm.scanOr.shape)
    # Linear fit to scanlines
    for k in range(sm.numImages):
        # need to offset 1 here for scanOr?
        ppx, *_ = np.linalg.lstsq(basisOr.T, sm.scanOr[k, 0, :]+1, rcond=None)
        ppy, *_ = np.linalg.lstsq(basisOr.T, sm.scanOr[k, 1, :]+1, rcond=None)
        scanOrLinear[k, 0, :] = basisOr.T @ ppx
        scanOrLinear[k, 1, :] = basisOr.T @ ppy

    # Subtract linear fit
    sm.scanOr -= scanOrLinear

    # Moving average of scanlines using KDE
    sm.scanOr = convolve(sm.scanOr, KDEorigin[:, None, None].T, 'same') * KDEnorm

    # Add linear fit back into to origins, and/or linear weighting
    sm.scanOr += scanOrLinear


def _global_phase_correlation(sm, scanOrStep, meanAbsDiff, densityCutoff, densityDist,
                              flagGlobalShiftIncrease,
                              minGlobalShift, refineInitialStep, alignStep):

    # save current origins, step size and score
    scanOrCurrent = sm.scanOr.copy();
    scanOrStepCurrent = scanOrStep.copy();
    meanAbsDiffCurrent = meanAbsDiff.copy();

    # Align to windowed image 0 or imageRef
    intensityMedian = np.median(sm.scanLines)
    cut = sm.imageDensity[0, ...] < densityCutoff
    min_d = np.minimum(distance_transform(cut) / densityDist, 1)
    densityMask = np.sin(min_d * np.pi/2)**2

    if sm.imageRef is None:
        smooth = sm.imageTransform[0,...]*densityMask + (1-densityMask)*intensityMedian
        imageFFT1 = np.fft.fft2(smooth)
        vecAlign = range(1, sm.numImages)
    else:
        smooth = sm.imageRef*densityMask + (1-densityMask)*intensityMedian
        imageFFT1 = np.fft.fft2(smooth)
        vecAlign = range(sm.numImages)

    # Align datasets 1 and higher to dataset 0, or align all images to imageRef
    for k in vecAlign:
        # Simple phase correlation
        cut = sm.imageDensity[k, ...] < densityCutoff
        min_d = np.minimum(distance_transform(cut) / 64, 1)
        densityMask = np.sin(min_d * np.pi/2)**2

        smooth = sm.imageTransform[k,...]*densityMask + (1-densityMask)*intensityMedian
        imageFFT2 = np.fft.fft2(smooth).conj()

        phase = np.angle(imageFFT1*imageFFT2)
        phaseCorr = np.abs(np.fft.ifft2(np.exp(1j*phase)))

        # Get peak maximum
        xInd, yInd = np.unravel_index(phaseCorr.argmax(), phaseCorr.shape)

        # Compute relative shifts. No -1 shift needed here.
        nr, nc = sm.imageSize
        dx = (xInd + nr/2) % nr - nr/2
        dy = (yInd + nc/2) % nc - nc/2

        # Only apply shift if it is larger than 2 pixels
        if (abs(dx) + abs(dy)) > minGlobalShift:
            # apply global origin shift, if possible
            xNew = sm.scanOr[k, 0, :] + dx
            yNew = sm.scanOr[k, 1, :] + dy

            # Verify shifts are within image boundaries
            withinBoundary = (xNew.min() >= 0) & (xNew.max() < nr-2) &\
                             (yNew.min() >= 0) & (yNew.max() < nc-2)
            if withinBoundary:
                # sMerge changed!
                sm.scanOr[k, 0, :] = xNew
                sm.scanOr[k, 1, :] = yNew

                # Recompute image with new origins
                # sMerge changed!
                sm = SPmakeImage(sm, k)

                # Reset search values for this image
                scanOrStep[k, :] = refineInitialStep

        if not flagGlobalShiftIncrease:
            # Verify global shift did not make mean abs. diff. increase.
            imgT_mean = sm.imageTransform.mean(axis=0)
            Idiff = np.abs(sm.imageTransform - imgT_mean).mean(axis=0)
            dmask = sm.imageDensity.min(axis=0) > densityCutoff
            img_mean = np.abs(sm.scanLines).mean()
            meanAbsDiffNew = Idiff[dmask].mean() / img_mean

            # sMerge changed!
            if meanAbsDiffNew < meanAbsDiffCurrent:
                # If global shift decreased mean absolute different, keep.
                sm.stats[alignStep-1, :] = np.array([alignStep-1, meanAbsDiff])
            else:
                # If global shift incresed mean abs. diff., return origins
                # and step sizes to previous values.
                sm.scanOr = scanOrCurrent
                scanOrStep = scanOrStepCurrent


def _plot(sm):
    imagePlot = (sm.imageTransform*sm.imageDensity).sum(axis=0)
    dens = sm.imageDensity.sum(axis=0)
    mask = dens > 0
    imagePlot[mask] /= dens[mask]

    # Scale intensity of image
    mask = dens > 0.5
    imagePlot -= imagePlot[mask].mean()
    imagePlot /= np.sqrt(np.mean(imagePlot[mask]**2))

    fig, ax = plt.subplots()
    ax.matshow(imagePlot, cmap='gray')

    # RGB colours
    cvals = np.array([[1, 0, 0],
                      [0, 0.7, 0],
                      [0, 0.6, 1],
                      [1, 0.7, 0],
                      [1, 0, 1],
                      [0, 0, 1]])

    # put origins on plot
    for k in range(sm.numImages):
        x = sm.scanOr[k, 1, :]
        y = sm.scanOr[k, 0, :]
        c = cvals[k % cvals.shape[0], :]

        ax.plot(x, y, marker='.', markersize=12, linestyle='None', color=c)

    # Plot statistics
    if sm.stats.shape[0] > 1:
        fig, ax = plt.subplots()
        ax.plot(sm.stats[:, 0], sm.stats[:, 1]*100, color='red', linewidth=2)
        ax.set_xlabel('Iteration [Step Number]')
        ax.set_ylabel('Mean Absolute Difference [%]')

    return
