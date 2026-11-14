
#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function, division, absolute_import

from polymer.level2 import Level2_file
from polymer.utils import safemove

from netCDF4 import Dataset, default_fillvals

import tempfile
import numpy as np
import re

from os.path import exists, dirname, join, basename
from os import remove
from shutil import rmtree


class Level2_NETCDF(Level2_file):
    '''
    Level2 in NetCDF format.

    Supports:
        - SNAP automatic band grouping
        - Spectral wavelength metadata
        - POLYMER naming conventions
        - Block-wise writing
        - NetCDF compression
    '''

    def __init__(
        self,
        filename=None,
        ext='.nc',
        tmpdir=None,
        outdir=None,
        overwrite=False,
        datasets=None,
        compress=True,
        format='NETCDF4',
    ):

        self.filename = filename
        self.overwrite = overwrite
        self.datasets = datasets
        self.compress = compress
        self.outdir = outdir

        self.initialized = False
        self.varlist = {}

        self.ext = ext
        self.__tmpdir = tmpdir
        self.tmpdir = None
        self.tmpfilename = None

        self.format = format

        # Spectral metadata
        self.spectral_wavelengths = {}

        # Spectral parameter groups
        self.spectral_groups = []

    # ---------------------------------------------------------
    # INITIALIZATION
    # ---------------------------------------------------------

    def init(self, level1):

        super(self.__class__, self).init(level1)

        # Obtain actual SRF-derived wavelengths from Level1 MSI
        self.spectral_wavelengths = {
            int(band): float(wavelength)
            for band, wavelength in getattr(
                level1, 'wav', {}
            ).items()
        }

        if self.__tmpdir is None:
            tmpdir = dirname(self.filename)
        else:
            tmpdir = tempfile.mkdtemp(
                dir=self.__tmpdir,
                prefix='level2_netcdf4_tmp_'
            )

            self.tmpdir = tmpdir

        self.tmpfilename = join(
            tmpdir,
            basename(self.filename) + '.tmp'
        )

        self.root = Dataset(
            self.tmpfilename,
            'w',
            format=self.format
        )

    # ---------------------------------------------------------
    # SPECTRAL NAME HANDLING
    # ---------------------------------------------------------

    def get_spectral_info(self, name):
        '''
        Identify spectral parameter and band from variable name.

        Supports:
            Rtoa443
            Rtoa_443

        Returns:
            parameter, band, normalized variable name
        '''

        for band in sorted(
            self.spectral_wavelengths,
            key=lambda x: len(str(x)),
            reverse=True
        ):

            suffix = '_' + str(band)

            if name.endswith(suffix):

                parameter = name[:-len(suffix)]

                if parameter:
                    return (
                        parameter,
                        band,
                        name
                    )

            suffix = str(band)

            if name.endswith(suffix):

                parameter = name[:-len(suffix)]

                if parameter:
                    normalized_name = '{}_{}'.format(
                        parameter,
                        band
                    )

                    return (
                        parameter,
                        band,
                        normalized_name
                    )

        return None, None, name

    def register_spectral_group(self, name):

        if name not in self.spectral_groups:
            self.spectral_groups.append(name)

    # ---------------------------------------------------------
    # WRITE BLOCK
    # ---------------------------------------------------------

    def write_block(self, name, data, S, attrs=None):
        '''
        Write data into NetCDF variables.

        Three-dimensional spectral arrays are separated
        into individual wavelength bands.
        '''

        if attrs is None:
            attrs = {}

        # -----------------------------------------------------
        # Split spectral arrays into individual bands
        # -----------------------------------------------------

        if data.ndim == 3:

            if data.shape[-1] == len(self.bands):

                self.register_spectral_group(name)

                for i, band in enumerate(self.bands):

                    sdsname = '{}_{}'.format(
                        name,
                        int(band)
                    )

                    self.write_block(
                        sdsname,
                        data[:, :, i],
                        S,
                        attrs
                    )

            else:

                for i in range(data.shape[-1]):

                    sdsname = '{}{}'.format(name, i)

                    self.write_block(
                        sdsname,
                        data[:, :, i],
                        S,
                        attrs
                    )

            return

        # -----------------------------------------------------
        # Initialize NetCDF dimensions
        # -----------------------------------------------------

        if not self.initialized:

            self.root.createDimension('x', self.shape[1])
            self.root.createDimension('y', self.shape[0])

            self.initialized = True

        assert data.ndim == 2

        # -----------------------------------------------------
        # Identify spectral metadata
        # -----------------------------------------------------

        parameter, band, variable_name = (
            self.get_spectral_info(name)
        )

        if band is not None:

            self.register_spectral_group(parameter)

        # -----------------------------------------------------
        # Data type conversion
        # -----------------------------------------------------

        if self.format == 'NETCDF4_CLASSIC':

            if data.dtype == np.uint16:
                typ = np.int16

            elif data.dtype == np.uint32:
                typ = np.int32

            else:
                typ = data.dtype

        else:
            typ = data.dtype

        fill_value = default_fillvals[
            np.dtype(typ).kind +
            str(np.dtype(typ).itemsize)
        ]

        # -----------------------------------------------------
        # Create NetCDF variable
        # -----------------------------------------------------

        if variable_name not in self.varlist:

            var = self.root.createVariable(
                variable_name,
                typ,
                ['y', 'x'],
                fill_value=fill_value,
                zlib=self.compress
            )

            # Preserve original POLYMER attributes
            if attrs:
                var.setncatts(attrs)

            # -------------------------------------------------
            # SNAP spectral wavelength metadata
            # -------------------------------------------------

            if band is not None:

                wavelength = (
                    self.spectral_wavelengths[band]
                )

                var.setncattr(
                    'wavelength',
                    wavelength
                )

                var.setncattr(
                    'radiation_wavelength',
                    wavelength
                )

                var.setncattr(
                    'radiation_wavelength_unit',
                    'nm'
                )

            self.varlist[variable_name] = var

        # -----------------------------------------------------
        # Replace NaNs with NetCDF fill values
        # -----------------------------------------------------

        data_to_write = np.asarray(data).copy()

        if np.issubdtype(typ, np.unsignedinteger):

            data_to_write[data_to_write < 0] = fill_value

        elif np.issubdtype(typ, np.floating):

            data_to_write[
                np.isnan(data_to_write)
            ] = fill_value

        # -----------------------------------------------------
        # Write block
        # -----------------------------------------------------

        self.varlist[variable_name][
            S[0], S[1]
        ] = data_to_write

    # ---------------------------------------------------------
    # FINISH
    # ---------------------------------------------------------

    
    def finish(self, params):
        for key, value in params.items():
            self.root.setncattr(key, str(value))
    
        # BEAM/SNAP metadata profile
        self.root.setncattr('product_type', 'NetCDF')
        self.root.setncattr('metadata_profile', 'beam')
        self.root.setncattr('metadata_version', '0.5')
        self.root.setncattr('title', 'NetCDF/CF Data Product')
        self.root.setncattr('Conventions', 'CF-1.7')
    
        # SNAP automatic band grouping.
        # Rwmod must precede Rw because both share the prefix Rw.
        preferred_order = [
            'Rtoa', 'Rprime', 'Tmol', 'Rwmod', 'Rw', 'Ratm'
        ]
    
        groups = [g for g in preferred_order if g in self.spectral_groups]
        groups += [g for g in self.spectral_groups if g not in groups]
    
        self.root.setncattr('auto_grouping', ':'.join(groups))
    
        # BEAM geocoding metadata, using POLYMER variable names.
        geocoding = '''<ComponentGeoCoding>
          <ForwardCodingKey>FWD_PIXEL</ForwardCodingKey>
          <InverseCodingKey>INV_PIXEL_QUAD_TREE</InverseCodingKey>
          <GeoChecks>ANTIMERIDIAN</GeoChecks>
          <GeoCRS>GEOGCS["WGS84(DD)", DATUM["WGS84", SPHEROID["WGS84", 6378137.0, 298.257223563]], PRIMEM["Greenwich", 0.0], UNIT["degree", 0.017453292519943295], AXIS["Geodetic longitude", EAST], AXIS["Geodetic latitude", NORTH], AUTHORITY["EPSG","4326"]]</GeoCRS>
          <LonVariableName>longitude</LonVariableName>
          <LatVariableName>latitude</LatVariableName>
          <RasterResolutionKm>1</RasterResolutionKm>
          <OffsetX>0.5</OffsetX>
          <OffsetY>0.5</OffsetY>
          <SubsamplingX>1.0</SubsamplingX>
          <SubsamplingY>1.0</SubsamplingY>
              </ComponentGeoCoding>'''
    
        self.root.setncattr('geocoding', geocoding)
    
        self.root.close()
        safemove(self.tmpfilename, self.filename)

    # ---------------------------------------------------------
    # ATTRIBUTES
    # ---------------------------------------------------------

    def attributes(self):

        attrs = {}

        attrs['l2_filename'] = self.filename
        attrs['l2_format'] = self.format

        return attrs

    # ---------------------------------------------------------
    # CONTEXT MANAGER
    # ---------------------------------------------------------

    def __enter__(self):

        return self

    def __exit__(self, *args):

        self.cleanup()

    # ---------------------------------------------------------
    # CLEANUP
    # ---------------------------------------------------------

    def cleanup(self):

        if (
            self.tmpfilename is not None
            and exists(self.tmpfilename)
        ):

            remove(self.tmpfilename)

        if self.tmpdir is not None:

            rmtree(self.tmpdir)
