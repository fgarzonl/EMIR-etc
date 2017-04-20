#!/usr/bin/env python
"""
Created:
Author: ???
Date: ???
Description: (LRP)
EMIR ETC python main script. These routines consists of an underlying python
script to claculate exposure times for EMIR Photometry and Spectroscopy with
a wrapper (also in python) to make the scripts usable online

The underlying python script was written by Carlos Gonzalez-Fernandez
(cambridge) and the wrapper was written by Matteo Miluzio (ESAC)

v1.0.8 14-04-2017
    Standard structure for all filter files
    Improved standardisation of etc_modules
    Added addtional output for spectroscopy
    Corrected bugs that distored S/N estimate
    Added new sky emission and transmission files at below 1.0um

v1.0.7 20-03-2017
    Used standard structure for (some) filter files
    (others need to be implemented)
    Correct a bug regarding the median values of the S/N

v.1.0.6 27-01-2017
    Updated the previous change to the resolution element calculation.
    More updates of code standardisation (still not complete!)

v.1.0.5 09-12-2016
    In this file, updated the way the resolution element is calculated in
    getSpecSton

v.1.0.4 08-12-2016
    Major updates of code standardisation
    Merged with ETC from Marc Balcells
    Much more ouput for spec. mode

v.1.0.3 05-12-2016
    Included fix for using 'Model file' that now does not depend upon the
    input magnitude
    Added F123M filter at request of Marc Balcels
    Updated 07-12-2016 to correct F123M transmission

TODO: Make better naming choices for variables
Work out where the slowest parts of this code are
Trim down the ETC to speed up the process

Author: LRP lpatrick@iac.es
Date: 28-11-2016
Description:
Updated to correct the output exposure times in output xml file and figures
Updated to add the Y filter as an option in photometry
Added version numer as v1.0

"""
import numpy as np
import sys
import xml.etree.ElementTree as ET
from optparse import OptionParser

import emir_guy
import etc_config as con
import etc_modules as mod
from etc_classes import SpecCurve

import matplotlib
matplotlib.use('Agg')  # Do we actually need agg?
import matplotlib.pylab as plt

description = ">> Exposure Time Calculator for EMIR. Contact Lee Patrick"
usage = "%prog [options]"

if len(sys.argv) == 1:
    print(help)
    sys.exit()
parser = OptionParser(usage=usage, description=description)
parser.add_option("-d", "--directory", dest="directory",
                  default='', help='Path of the xml file \n  [%default]')
option, args = parser.parse_args()


class EmirGui:
    """GUI for the ETC"""

    def __init__(self):
        """Initialise"""
        # When the application is loaded, all the fixed elements of the system
        # (optics, etc.) plus the sky curves are loaded
        global ff
        config_files = con.get_config()
        # Changed to 80000 by LRP on 04-04-2017
        self.ldo_hr = (8000 + np.arange(100001)*0.2)*1e-4

        # Fixed elements of the system

        qe = SpecCurve(config_files['qe'])
        optics = SpecCurve(config_files['optics'])
        tel = SpecCurve(config_files['telescope'])

        # Addition from MCB's ETC by LRP
        self.qe_hr = qe.interpolate(self.ldo_hr)
        self.optics_hr = optics.interpolate(self.ldo_hr)
        self.tel_hr = tel.interpolate(self.ldo_hr)
        self.trans = self.qe_hr*self.optics_hr*self.tel_hr
        # End addition

        # Vega spectrum for normalizations
        self.vega = SpecCurve(config_files['vega']).interpolate(self.ldo_hr)

        try:
            ff = emir_guy.readxml(args[0] + '.xml')
        except:
            print("ERROR opening XML file")
            exit()

        emir_guy.load(self)
        emir_guy.check_inputs(ff, args[0])

        # Functions for options:
        if ff['operation'] == 'Photometry':
            self.doPhotometry()
        elif ff['operation'] == 'Spectroscopy':
            self.doSpectroscopy()

    def doPhotometry(self):
        """Photometry initialisations"""
        self.mode_oper = 'ph'

        # Obtaining configuration parameters from GUI
        self.mag = float(ff['magnitude'])
        self.seeing = float(ff['seeing'])
        self.airmass = float(ff['airmass'])
        self.sky_t, self.sky_e = mod.interpolatesky(self.airmass, self.ldo_hr)
        self.filtname = ff['photo_filter']

        self.buildObj()
        # We have to break the texp into its bits
        temp = ff['photo_exp_time'].split('-')
        if len(temp) == 1:
            # This creates a one length array, so that len(texp) doesn't crash
            self.texp = np.array([float(temp[0])])
            self.timerange = 'Single'
        else:
            tmin = float(temp[0])
            tmax = float(temp[1])
            self.texp = tmin + (tmax - tmin)*np.arange(100)/99.
            self.timerange = 'Range'

        # Number of frames
        self.nobj = float(ff['photo_nf_obj'])
        self.nsky = float(ff['photo_nf_sky'])

        # Filter transmission curve
        self.filt = con.get_filter(self.filtname)
        self.filt_hr = self.filt.interpolate(self.ldo_hr)

        # Calling the function that calculates the STON
        ston, signal_obj, signal_sky, saturated,\
            params = self.getPhotSton(self.texp, self.nobj, self.nsky)
        if self.timerange == 'Range':
            # self.printResults(self.texp,ston,saturated)
            self.printXML(self.texp, signal_obj, signal_sky,
                          ston, saturated, **params)
            plt.plot(self.texp*self.nobj, ston)  # Update by LRP 28-11-2016
            plt.xlabel('Exposure time (seconds)')
            if ff['source_type'] == 'Point':
                plt.ylabel('S/N')
            if ff['source_type'] == 'Extended':
                plt.ylabel('S/N per pixel')
            plt.savefig(args[0] + '_photo.png')
        else:
            self.printXML(self.texp, signal_obj, signal_sky,
                          ston, saturated, **params)
            # TODO: Create some meaniningful graphic output!

    def doSpectroscopy(self):
        """Spectroscopy initialisations"""
        self.mode_oper = 'sp'

        # Obtaining configuration parameters from GUI
        self.mag = float(ff['magnitude'])
        self.seeing = float(ff['seeing'])
        self.airmass = float(ff['airmass'])
        self.slitwidth = float(ff['spec_slit_width'])
        self.slitloss = mod.slitpercent(self.seeing, self.slitwidth)

        self.sky_t, self.sky_e = mod.interpolatesky(self.airmass, self.ldo_hr)
        self.grismname = ff['spec_grism']
        self.buildObj()

        #    We have to break the texp into its bits
        temp = ff['spec_exp_time'].split('-')
        if len(temp) == 1:
            # This creates a one length array, so that len(texp) does't crash
            self.texp = np.array([float(temp[0])])
            self.timerange = 'Single'
        else:
            tmin = float(temp[0])
            tmax = float(temp[1])
            self.texp = tmin + (tmax - tmin)*np.arange(10)/9.
            self.timerange = 'Range'

        # Number of frames
        self.nobj = float(ff['spec_nf_obj'])
        self.nsky = float(ff['spec_nf_sky'])

        # The filter transmission curve
        #
        self.specres, self.grism, self.filt = con.get_grism(self.grismname)
        self.filt_hr = self.filt.interpolate(self.ldo_hr)
        self.grism_hr = self.grism.interpolate(self.ldo_hr)
        self.dispersive = self.filt_hr*self.grism_hr
        # Addition from MCB's ETC by LRP
        self.efftotal_hr = self.tel_hr*self.optics_hr*self.filt_hr*\
            self.grism_hr*self.qe_hr
        #
        #    Calling the function that calculates the STON
        #
        if self.timerange == 'Single':
            ston, src_cnts, sky_cnts, sp,\
                saturated, params = self.getSpecSton(self.texp, self.nobj,
                                                     self.nsky)
            if ff['template'] == 'Emission line':
                self.printXML(self.texp, [np.max(src_cnts)],
                              [np.median(sky_cnts[np.nonzero(sky_cnts)])],
                              [np.max(ston)],
                              saturated, **params)
            else:
                self.printXML(self.texp,
                              [np.median(src_cnts[np.nonzero(src_cnts)])],
                              [np.median(sky_cnts[np.nonzero(sky_cnts)])],
                              [np.median(ston[np.nonzero(ston)])],
                              saturated, **params)

            # Create some figures:
            plt.figure(1, figsize=(15., 10.))
            plt.subplot(321)
            plt.plot(self.ldo_px, ston, color='b')
            med_spec = np.median(ston[np.nonzero(ston)])
            x_med = np.linspace(self.ldo_px[0], self.ldo_px[-1])
            plt.plot(x_med, np.linspace(med_spec, med_spec), color='r')
            plt.xlim(self.ldo_px[0], self.ldo_px[-1])
            plt.xlabel('Wavelength (micron)')
            if ff['source_type'] == 'Point':
                plt.ylabel('S/N')
            if ff['source_type'] == 'Extended':
                plt.ylabel('S/N per pixel')

            plt.subplot(323)
            plt.plot(self.ldo_px, src_cnts)
            plt.plot(self.ldo_px, sky_cnts)
            plt.xlim(self.ldo_px[0], self.ldo_px[-1])
            plt.xlabel('Wavelength (micron)')
            plt.ylabel('Source ADU/pixel')

            plt.subplot(325)
            plt.plot(self.ldo_px, sp)
            plt.xlim(self.ldo_px[0], self.ldo_px[-1])
            plt.xlabel('Wavelength (micron)')
            plt.ylabel('Normalized src flux')

            plt.subplot(322)
            plt.plot(self.ldo_hr, self.qe_hr, '-r', label='Det')
            plt.plot(self.ldo_hr, self.grism_hr, '--c', label='Grism')
            plt.plot(self.ldo_hr, self.filt_hr, '-c', label='Filter')
            plt.plot(self.ldo_hr, self.optics_hr, '-b', label='Optics')
            plt.plot(self.ldo_hr, self.tel_hr, '--b', label='Tel')
            plt.plot(self.ldo_hr, self.efftotal_hr, '-k', label='Qtot')
            plt.xlim(self.ldo_px[0], self.ldo_px[-1])
            plt.legend(bbox_to_anchor=(1.3, 1.05))
            plt.xlabel('Wavelength (micron)')
            plt.ylabel('efficiency / band')

            plt.subplot(324)
            plt.plot(self.ldo_hr, self.efftotal_hr, '-k', label='Qtot')
            plt.xlim(self.ldo_px[0], self.ldo_px[-1])
            plt.legend(bbox_to_anchor=(1.3, 1.05))
            plt.xlabel('Wavelength (micron)')
            plt.ylabel('Eff Tel to Det')

            plt.subplot(326)
            plt.plot(self.ldo_hr, self.qe_hr, '-r', label='Det')
            plt.plot(self.ldo_hr, self.grism_hr, '--c', label='Grism')
            plt.plot(self.ldo_hr, self.filt_hr, '-c', label='Filter')
            plt.plot(self.ldo_hr, self.optics_hr, '-b', label='Optics')
            plt.plot(self.ldo_hr, self.tel_hr, '--b', label='Tel')
            plt.plot(self.ldo_hr, self.efftotal_hr, '-k', label='Qtot')
            plt.legend(bbox_to_anchor=(1.3, 1.05))
            plt.xlabel('Wavelength (micron)')
            plt.ylabel('Efficiency full EMIR range')
            # End of figures

        if self.timerange == 'Range':

            ston = np.zeros_like(self.texp)
            saturated = np.zeros_like(self.texp)
            src_med_cnts = np.zeros_like(self.texp)
            sky_med_cnts = np.zeros_like(self.texp)
            for i in range(len(self.texp)):
                temp, src_cnts, sky_cnts, sp, satur, params\
                    = self.getSpecSton(self.texp[i], self.nobj, self.nsky)
                ston[i] = np.median(temp[np.nonzero(temp)])
                saturated[i] = satur
                src_med_cnts[i] = np.median(src_cnts[np.nonzero(src_cnts)])
                sky_med_cnts[i] = np.median(sky_cnts[np.nonzero(sky_cnts)])
                self.printXML(self.texp, src_med_cnts, sky_med_cnts,
                              ston, saturated, **params)
                # self.printXML(self.texp,src_cnts,sky_cnts,ston,saturated,params)

            temp, src_cnts, sky_cnts, sp, temp2, params\
                = self.getSpecSton(self.texp[i], self.nobj, self.nsky)
            # Additional figure for an inputed range of exposure times
            plt.figure(1)
            plt.subplot(211)
            plt.plot(self.ldo_px, temp)
            plt.xlabel('Wavelength (micron)')
            if ff['source_type'] == 'Point':
                plt.ylabel('S/N at texp = {0:.1f}'.format(self.texp[-1]))
            if ff['source_type'] == 'Extended':
                plt.ylabel('S/N per pixel at texp = {0:.1f}'
                           .format(self.texp[-1]))

            plt.subplot(212)
            plt.plot(self.ldo_px, sp)
            plt.xlabel('Wavelength (micron)')
            plt.ylabel('Normalized src flux')
        plt.savefig(args[0]+'_spec.png')

    def getSpecSton(self, texp=1, nobj=1, nsky=1):
        """For Spectroscopy Get SignaltoNoise (Ston)"""
        params = con.get_params()

        # The skymagnitude works because the catchall is Ks, and
        # the other grisms have the same name as their respective filters
        self.mag_sky = con.get_skymag(self.grismname)

        # 1.- Scale object & sky with Vega. Note: the per angstrom dependence
        # of the SED is removed later, when the ldo per pixel is calculated

        # In case of an emission line, there is no need to re-normalize
        # ####################################################################
        # CGF 02/12/16
        if ff['template'] == 'Emission line':
            no = self.obj*params['area']
        elif (ff['template'] == 'Model file') & \
                (self.obj_units != 'normal_photon'):
            no = self.obj*params['area']
        else:
            no = (10**(-1*self.mag/2.5))*\
                mod.vega(self.obj, self.vega, self.filt_hr)*params['area']

        # ####################################################################

        # Sky
        ns = (10**(-1*self.mag_sky/2.5))*\
            mod.vega(self.sky_e, self.vega, self.filt_hr)*params['area']

        #    2.- Calculate the wavelengths visible in the detector
        self.cenwl = (self.ldo_hr*self.dispersive).sum()/(self.dispersive).sum()
        self.delta_px = (self.cenwl/self.specres)/3.
        self.res_ele = self.delta_px*(self.slitwidth/(params['scale']))
        self.ldo_px = (np.arange(2048) - 1024)*self.delta_px + self.cenwl

        #    3.- Convolve the SEDs with the proper resolution
        #        Delta(lambda) is evaluated at the central wavelength

        con_obj = mod.convolres(self.ldo_hr,
                                texp*self.slitloss*(no*self.dispersive*
                                                    self.trans*
                                                    self.sky_t),
                                self.res_ele)
        con_sky = mod.convolres(self.ldo_hr,
                                texp*(ns*self.dispersive*self.trans),
                                self.res_ele)

        # Changes suggested by CGF 27-01-2017
        # Dispersion
        # This is fixed for each grism
        # self.delta_px = self.ldo_px[1] - self.ldo_px[0]
        # end changes

        # self.cenwl = (self.ldo_hr*self.dispersive).sum()/(self.dispersive).sum()
        # self.res_ele = (self.cenwl/self.specres)/3.
        # # Calculation of resolution element updated by LRP,FGL, GCF 09-12-2016
        # # self.res_ele = ((self.cenwl/self.specres)/self.slitwidth)\
        # #     *params['scale']
        # self.ldo_px = (np.arange(2048) - 1024)*self.res_ele + self.cenwl
        # self.delta_px = self.ldo_px[1] - self.ldo_px[0]

        #    4.- Interpolate SEDs over the observed wavelengths
        #    and estimate the STON

        sp_sky = self.delta_px*mod.spec_int(self.ldo_hr,
                                            con_sky*params['scale']**2,
                                            self.ldo_px)

        if ff['source_type'] == 'Point':
            sp_obj = self.delta_px*mod.spec_int(self.ldo_hr, con_obj,
                                                self.ldo_px)
            im_spec = np.zeros((len(sp_obj), 100))
            im_sky = np.zeros((len(sp_obj), 100))
            total_noise = np.zeros((len(sp_obj), 100))

            # No sky frame implies that reduction is as good
            # as taking one single sky frame

            if nsky == 0:
                nsky_t = 1
            else:
                nsky_t = nsky

            for i in range(len(sp_obj)):
                im_spec[i, :] = mod.getspread(sp_obj[i], self.seeing, 0) + sp_sky[i]
                im_sky[i, :] = sp_sky[i]

                spec_noise = mod.getnoise(im_spec[i, :], texp)/np.sqrt(nobj)
                sky_noise = mod.getnoise(im_sky[i, :], texp)/np.sqrt(nsky_t)
                total_noise[i, :] = np.sqrt(spec_noise**2 + sky_noise**2)

            r = np.abs(np.arange(100) - 50)
            # Receta de Peter
            ind = (np.where(r <= 1.2*self.seeing/params['scale']))[0]

            # This is the old version, I think the noise is wrong,
            # as the summation is not 2D
            # ston_sp=im_spec[:,ind].sum(1)/np.sqrt((im_noise[ind]**2).sum())
            # 29/12/2013
            ston_sp = (im_spec - im_sky)[:, ind].sum(1)/\
                np.sqrt((total_noise[:, ind]**2).sum(1))
            satur = mod.checkforsaturation(im_spec[:, ind])

        elif ff['source_type'] == 'Extended':
            sp_obj = self.delta_px*mod.spec_int(self.ldo_hr,
                                                con_obj*params['scale']**2,
                                                self.ldo_px)
            im_noise = np.sqrt((mod.getnoise(sp_obj + sp_sky, texp)/
                                np.sqrt(nobj))**2 +
                               (mod.getnoise(sp_sky, texp)/np.sqrt(nsky))**2)

            satur = mod.checkforsaturation(sp_obj + sp_sky)
            ston_sp = sp_obj/im_noise

        # Calculate original spectrum for display

        con_0 = mod.convolres(self.ldo_hr, self.slitloss*texp*no,
                              self.delta_px)
        # con_0 = mod.convolres(self.ldo_hr, self.slitloss*texp*no,
        #                       self.cenwl/self.specres)
        if ff['source_type'] == 'Point':
            sp_0 = mod.spec_int(self.ldo_hr, con_0, self.ldo_px)*self.delta_px

        elif ff['source_type'] == 'Extended':
            sp_0 = self.delta_px*mod.spec_int(self.ldo_hr,
                                              con_0*params['scale']**2,
                                              self.ldo_px)
        # Update by LRP from MBC, this function now returns more parameters
        # MB 2016-09-29 return source counts as well
        # return ston_sp, sp_0/sp_0.max(), satur

        obj_cnts = sp_obj/params['gain']
        sky_cnts = sp_sky/params['gain']
        return ston_sp, obj_cnts, sky_cnts, sp_0/sp_0.max(), satur, params

    def getPhotSton(self, texp=1, nobj=1, nsky=1):
        """For Photometry"""
        params = con.get_params()
        self.mag_sky = con.get_skymag(self.filtname)
        ston = np.zeros_like(texp)
        satur = np.zeros_like(texp)
        # Added by LRP from MBC's ETC
        signal_obj = np.zeros_like(texp)
        signal_sky = np.zeros_like(texp)

        #    1.- Scale object & sky with Vega

        trans_to_scale = self.filt_hr*self.trans
        # no=(10**(-1*self.mag/2.5))*mod.vega(self.obj,self.vega,trans_to_scale)\
        #     *params['area']*float(self.ldo_hr[1]-self.ldo_hr[0])
        #
        #######################################################################
        #
        # CGF 02/12/16
        #
        if ff['template'] == 'Emission line':
            no = self.obj*params['area']*float(self.ldo_hr[1] - self.ldo_hr[0])
        elif (ff['template'] == 'Model file') & \
                (self.obj_units != 'normal_photon'):
            no = self.obj*params['area']*float(self.ldo_hr[1] - self.ldo_hr[0])
        else:
            no = (10**(-1*self.mag/2.5))\
                *mod.vega(self.obj, self.vega, trans_to_scale)\
                *params['area']*float(self.ldo_hr[1] - self.ldo_hr[0])

        #######################################################################
        ns = (10**(-1*self.mag_sky/2.5))*\
            mod.vega(self.sky_e, self.vega, trans_to_scale)\
            *params['area']*float(self.ldo_hr[1]-self.ldo_hr[0])

        if ff['template'] == 'Emission line':
            no = no + self.obj*params['area']*float(self.ldo_hr[1] -
                                                    self.ldo_hr[0])

        #  2.- Calculate total fluxes through passbands.
        #  The filter appears here and in step 1 because there is used
        #  to calculate the flux under it in order to normalize the
        #  spectra with Vega. Here is used to calculate total fluxes.

        fl_obj = texp*(no*self.filt_hr*self.sky_t).sum()
        fl_sky = texp*(ns*self.filt_hr).sum()*params['scale']**2

        # In case of point-like source, we need to estimate the aperture
        # to properly account for the effect of the RON and sky.
        # In the case of extended sources, the estimated values are per pixel

        if ff['source_type'] == 'Point':
            # 3.- Synthethic image generation
            # An "image" of radii values from the center is used to see how
            # many pixels fall inside the seeing ring.

            im_r = np.zeros((100, 100))
            x = np.arange(100)
            for i in range(0, 100):
                im_r[i, ] = np.sqrt((float(i) - 50.0)**2 + (x - 50.0)**2)

            # From Peter: a good guesstimate of the aperture is 1.2*seeing

            ind = np.where(im_r <= 0.5*1.2*self.seeing / params['scale'])

            #    The actual STON calculation

            for i in range(len(texp)):
                im_obj = mod.getspread(fl_obj[i], self.seeing, 1) + fl_sky[i]
                im_sky = np.zeros_like(im_obj) + fl_sky[i]

                if nsky == 0:
                    # For no sky frames is assumed that the reduction
                    # is as good as taking a single sky frame.
                    sky_noise = mod.getnoise(im_sky, texp[i])
                else:
                    sky_noise = mod.getnoise(im_sky, texp[i]) / np.sqrt(nsky)

                obj_noise = mod.getnoise(im_obj, texp[i]) / np.sqrt(nobj)
                total_noise = np.sqrt(sky_noise**2 + obj_noise**2)
                ston[i] = (im_obj - im_sky)[ind].sum()\
                    / np.sqrt((total_noise[ind]**2).sum())
                satur[i] = mod.checkforsaturation(im_obj)

                # Added by LRP from MBC's ETC
                # MBC added 2016-11-28
                # total counts from source and sky in aperture
                signal_obj[i] = (im_obj - im_sky)[ind].sum() / params['gain']
                signal_sky[i] = im_sky[ind].sum() / params['gain']
                # print('Signal_obj[i] {}'.format(signal_obj[i]))
                # print('Signal_sky[i] {}'.format(signal_sky[i]))

        elif ff['source_type'] == 'Extended':
            # For an extended sources calculate the flux per pixel
            fl_obj = fl_obj*params['scale']**2
            for i in range(len(texp)):
                im_obj = np.ones(1)*(fl_obj[i] + fl_sky[i])
                im_sky = np.ones(1)*fl_sky[i]

                if nsky == 0:
                    # For no sky frames is assumed that the reduction
                    # is as good as taking a single sky frame.
                    sky_noise = mod.getnoise(im_sky, texp[i])
                else:
                    sky_noise = mod.getnoise(im_sky, texp[i])/ np.sqrt(nsky)
                obj_noise = mod.getnoise(im_obj, texp[i])/ np.sqrt(nobj)
                total_noise = np.sqrt(sky_noise**2 + obj_noise**2)
                ston[i] = (im_obj-im_sky) / total_noise
                satur[i] = mod.checkforsaturation(im_obj)
                # Added by LRP from MBC's ETC
                # MBC added 2016-11-28
                signal_obj[i] = (im_obj - im_sky) / params['gain']
                signal_sky[i] = im_sky / params['gain']

        return ston, signal_obj, signal_sky, satur, params

    def buildObj(self):
        """Build the SED from the input parameters"""
        # CGF 05/12/16
        # Default catchall so that the units are always defined
        self.obj_units = 'normal_photon'
        if ff['template'] == 'Model library':
            # CGF 05/12/16
            temp_curve = SpecCurve('libs/' + self.available[ff['model']])
            self.obj = temp_curve.interpolate(self.ldo_hr)
            self.obj_units = temp_curve.unity
        elif ff['template'] == 'Black body':
            self.bbteff = float(ff['body_temp'])
            self.obj = mod.bbody(self.ldo_hr, self.bbteff)
            # CGF 05/12/16
            self.obj_units = 'normal_photon'
        elif ff['template'] == 'Model file':
            # User loaded model
            # CGF 02/12/16
            temp_curve = SpecCurve(ff['model_file'])
            self.obj = temp_curve.interpolate(self.ldo_hr)
            self.obj_units = temp_curve.unity
        elif ff['template'] == 'Emission line':
            # LRP: I don't understand this temp buisness
            # ... Why do we have 3 loops that seem to do nothing???
            # It seems to think we can have multiple emission line inputs but
            # the php wrapper doesn't support this.
            #
            # The input can be several lines separated by commas
            #
            temp = ff['line_center'].split(',')
            self.lcenter = []
            for i in temp:
                self.lcenter.append(float(i))
            temp = ff['line_fwhm'].split(',')
            self.lwidth = []
            for i in temp:
                self.lwidth.append(float(i)*1e-4)
            temp = ff['line_peakf'].split(',')
            self.lflux = []
            for i in temp:
                self.lflux.append(float(i)*1e-16)

            # In case the number of inputs is different in any section

            n_valid = np.min([len(self.lcenter), len(self.lwidth),
                             len(self.lflux)])
            self.lcenter = self.lcenter[0:n_valid]
            self.lwidth = self.lwidth[0:n_valid]
            self.lflux = self.lflux[0:n_valid]
            self.obj = np.zeros_like(self.ldo_hr)
            for i in range(len(self.lflux)):
                self.obj += mod.emline(self.ldo_hr, self.lcenter[i],
                                       self.lwidth[i], self.lflux[i])
            ###################################################################
            # CGF 05/12/16
            self.obj_units = 'photon/s/m2/micron'
            # mod.emline outputs in photon/s/m2/micron

    def printXML(self, texp, signal_obj, signal_sky, ston, satur, **params):
        """
        A function to create the output XML files
        Updated to inlucde more output by LRP 08-12-2016
        Mainly taken from Marc Balcells' version of the ETC

        Would this would be quicker if we just had a few if statements and put
        output for each case together -- also may cause fewer errors!
        """
        output = ET.Element("output")

        if ff['operation'] == 'Photometry':
            fig_name = args[0] + "_photo.png"
        else:
            fig_name = args[0] + "_spec.png"

        ET.SubElement(output, "fig").text = fig_name

        ET.SubElement(output, "text").text = "SOURCE:"
        ET.SubElement(output, "text").text = "{0:s} Source (Vega Mag) = {1:.3f}".\
            format(ff['source_type'], self.mag)
        if ff['template'] == 'Model library':
            ET.SubElement(output, "text").text = "Template: Model library"
            ET.SubElement(output, "text").text= "Spectral Type: {0:s}".format(ff['model'])
        elif ff['template'] == 'Black body':
            ET.SubElement(output, "text").text = "Template: Black Body"
            ET.SubElement(output, "text").text = "Temperature = {0:.1f} K".format(float(ff['body_temp']))
        elif ff['template'] == 'Emission line':
            ET.SubElement(output, "text").text = "Template: Emission Line"
            ET.SubElement(output, "text").text = "Center = {0:s}, FWHM = {1:s}, Total line flux = {2:s}"\
                .format(ff['line_center'], ff['line_fwhm'], ff['line_peakf'])
        elif ff['template'] == 'Model file':
            ET.SubElement(output, "text").text = "Template: Model file"
            ET.SubElement(output, "text").text = "Model file = {0:s}".format(ff['model_file'])

        ET.SubElement(output, "text").text = "----------------------------------------------------------------"
        ET.SubElement(output, "text").text = "OBSERVATION:"
        ET.SubElement(output, "text").text = "Operation: {0:s}".format(ff['operation'])
        ET.SubElement(output, "text").text = "Exposure time(s) = {0:s}".format(ff['spec_exp_time'])
        ET.SubElement(output, "text").text = "Number of exposures: Object {0:d}, Sky {1:d}".format(int(self.nobj), int(self.nsky))
        ET.SubElement(output, "text").text = "----------------------------------------------------------------"
        ET.SubElement(output, "text").text = "TELESCOPE AND INSTRUMENT:"
        if ff['operation'] == 'Photometry':
            ET.SubElement(output, "text").text = "Filter: {0:s} ".format(self.filtname)
        else:
            ET.SubElement(output, "text").text = "Grism: {0:s}".format(self.grismname)
            ET.SubElement(output, "text").text = "Slit width = {0:.2f} arcsec".format(self.slitwidth)
        ET.SubElement(output, "text").text = "Telescope collecting area = {0:.1f} m<sup>2</sup>".format(params['area'])

        # ET.SubElement(output, "text").text = "----------------------------------------------------------------"
        # ET.SubElement(output, "text").text = "Detector: "
        ET.SubElement(output, "text").text = "Spatial scale = {0:.4f} arcsec/pix ".format(params['scale'])
        ET.SubElement(output, "text").text = "Readout noise = {0:.1f} e<sup>-</sup> ".format(params['RON'])
        ET.SubElement(output, "text").text = "Dark current = {0:.2f} e<sup>-</sup>/hr".format(params['DC'])
        ET.SubElement(output, "text").text = "Well depth = {0:.1f} e<sup>-</sup>".format(params['well'])
        ET.SubElement(output, "text").text = "Gain = {0:.2f} e<sup>-</sup>/ADU".format(params['gain'])
        ET.SubElement(output, "text").text = "----------------------------------------------------------------"
        ET.SubElement(output, "text").text = "OBSERVING CONDITIONS:"
        ET.SubElement(output, "text").text = "Airmass = {0:.2f}".format(self.airmass)
        ET.SubElement(output, "text").text = "Seeing = {0:.2f} arcsec FWHM".format(self.seeing)
        ET.SubElement(output, "text").text = "Sky brightness = {0:.2f} Vega mag / arcsec<sup>2</sup>".format(self.mag_sky)
        ET.SubElement(output, "text").text = " "
        ET.SubElement(output, "text").text = "----------------------------------------------------------------"
        ET.SubElement(output, "text").text= "RESULTS:"

        tabletext = ""
        if ff['operation']=='Spectroscopy':
            ET.SubElement(output, "text").text = "Wavelength coverage: {0:.2f} - {1:.2f} &mu;".format(self.ldo_px[0],self.ldo_px[-1])
            ET.SubElement(output, "text").text = "Dispersion {0:.2f} &Aring;/pix".format(self.delta_px*1e4)
            # ET.SubElement(output, "text").text = "Resolution element {0:.2f} &Aring;".format(self.cenwl*1e4/self.specres) 
            ET.SubElement(output, "text").text = "Resolution element {0:.2f} &Aring;".format(self.res_ele*1e4) 
            ET.SubElement(output, "text").text = "In-slit fraction {0:.4f} ".format(self.slitloss)
            # Diagnostics:
            ET.SubElement(output, "text").text = "Nominal Spectral resolution {0:.4f} ".format(self.specres)
            ET.SubElement(output, "text").text = "Achieved Spectral resolution {0:.4f} ".format(self.cenwl/self.res_ele)
            # ET.SubElement(output, "text").text = "Central lambda {0:.4f} ".format(self.cenwl)
        if self.timerange != 'Range':
            ET.SubElement(output, "text").text = "For {0:d} exposure(s) of {1:.1f} s: ".format(int(self.nobj),texp[0])

            if ff['template'] == 'Emission line':
                ET.SubElement(output, "text").text = "Maximum counts from object {0:.1f}, median from sky: {1:.1f}".format(signal_obj[0],signal_sky[0])
                ET.SubElement(output, "text").text = "Maximum S/N = {0:.1f}".format(ston[0])
                ET.SubElement(output, "text").text = "Effective gain = {0:.2f} ".format(params['gain']*self.nobj)
                # ET.SubElement(output, "text").text = "For time {0:.1f} s the expected S/N is {1:.1f}".format(texp[0]*self.nobj,ston[0])
            else:
                ET.SubElement(output, "text").text = "Median counts per pixel: from object = {0:.1f}, from sky = {1:.1f}".format(signal_obj[0],signal_sky[0])
                ET.SubElement(output, "text").text = "Median S/N per pixel = {0:.1f}".format(ston[0])
                ET.SubElement(output, "text").text = "Effective gain = {0:.2f} ".format(params['gain']*self.nobj)
                ET.SubElement(output, "text").text = "For time {0:.1f} s the expected median S/N is {1:.1f}".format(texp[0]*self.nobj, ston[0])
            if satur:
                ET.SubElement(output, "warning").text = "for time {0:.1f} s some pixels are saturated".format(texp[0]*self.nobj)
        else:
            tabletext += "\n\tFor the selected time range, the expected S/N per pixel are:"
            tabletext += "\n\t    t(s)\t     S/N\tSaturation?"
            tabletext += "\n\t----------------------"
            if ff['operation'] == 'Photometry':
                for i in range(0, 99, 10):
                    flags = 'No'
                    if satur[i]:
                        flags = 'Yes'
                    tabletext += '\n\t{0:8.1f}\t{1:8.1f}\t'\
                        .format(texp[i]*self.nobj, ston[i]) + flags
                flags = 'No'
                if satur[-1]:
                    flags = 'Yes'
                tabletext += '\n\t{0:8.1f}\t{1:8.1f}\t'\
                    .format(texp[-1]*self.nobj, ston[-1]) + flags
            else:
                for i in range(0, 9):
                    flags = 'No'
                    if satur[i]:
                        flags = 'Yes'
                    tabletext += '\n\t{0:8.1f}\t{1:8.1f}\t'\
                        .format(texp[i]*self.nobj, ston[i]) + flags

        tabletext += "\n"
        ET.SubElement(output, "table").text = tabletext
        emir_guy.indent(output)
        tree = ET.ElementTree(output)
        tree.write(args[0] + "_out.xml")

try:
    EmirGui()
except SystemExit:
    pass
except:
    emir_guy.generic_error(args[0])
exit()