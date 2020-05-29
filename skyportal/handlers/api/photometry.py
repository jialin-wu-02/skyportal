import numpy as np
import arrow
from astropy.time import Time
from astropy.table import Table
import pandas as pd
from marshmallow.exceptions import ValidationError
from baselayer.app.access import permissions, auth_or_token
from ..base import BaseHandler
from ...models import (
    DBSession, Photometry, Instrument, Source, Obj,
    PHOT_ZP, PHOT_SYS, Thumbnail
)

from ...schema import (PhotometryMag, PhotometryFlux)
from ...enum import ALLOWED_MAGSYSTEMS
import sncosmo

def nan_to_none(value):
    """Coerce a value to None if it is nan, else return value."""
    try:
        return None if np.isnan(value) else value
    except TypeError:
        return value

def allscalar(d):
    return all(np.isscalar(v) or v is None for v in d.values())


class PhotometryHandler(BaseHandler):
    @permissions(['Upload data'])
    def post(self):
        """
        ---
        description: Upload photometry
        requestBody:
          content:
            application/json:
              schema:
                oneOf:
                  - $ref: "#/components/schemas/PhotMagFlexible"
                  - $ref: "#/components/schemas/PhotFluxFlexible"
        responses:
          200:
            content:
              application/json:
                schema:
                  allOf:
                    - $ref: '#/components/schemas/Success'
                    - type: object
                      properties:
                        data:
                          type: object
                          properties:
                            ids:
                              type: array
                              items:
                                type: integer
                              description: List of new photometry IDs
        """

        data = self.get_json()

        if not isinstance(data, dict):
            return self.error('Top level JSON must be an instance of `dict`, got '
                              f'{type(data)}.')

        if allscalar(data):
            data = [data]

        try:
            df = pd.DataFrame(data)
        except ValueError as e:
            return self.error('Unable to coerce passed JSON to a series of packets. '
                              f'Error was: "{e}"')

        # pop out thumbnails and process what's left using schemas

        ids = []
        for i, row in df.iterrows():
            packet = row.to_dict()

            # coerce nans to nones
            for key in packet:
                packet[key] = nan_to_none(packet[key])

            try:
                phot = PhotometryFlux.load(packet)
            except ValidationError as e1:
                try:
                    phot = PhotometryMag.load(packet)
                except ValidationError as e2:
                    return self.error('Invalid input format: Tried to parse '
                                      f'{packet} as PhotometryFlux, got: '
                                      f'"{e1.normalized_messages()}." Tried '
                                      f'to parse {packet} as PhotometryMag, got:'
                                      f' "{e2.normalized_messages()}."')

            phot.packet = packet
            DBSession().add(phot)

            # to set up obj link
            DBSession().flush()

            time = arrow.get(Time(phot.mjd, format='mjd').iso)
            phot.obj.last_detected = max(
                time,
                phot.obj.last_detected
                if phot.obj.last_detected is not None
                else arrow.get("1000-01-01")
            )
            ids.append(phot.id)

        DBSession().commit()
        return self.success(data={"ids": ids})

    @auth_or_token
    def get(self, photometry_id):
        # Moved to bottom of page as f-string

        phot = Photometry.query.get(photometry_id)
        if phot is None:
            return self.error('Invalid photometry ID')
        # Ensure user/token has access to parent source
        _ = Source.get_if_owned_by(phot.obj_id, self.current_user)

        # get the desired output format
        format = self.get_query_argument('format', 'mag')
        outsys = self.get_query_argument('magsys', 'ab')

        retval = {
            'obj_id': phot.obj_id,
            'ra': phot.ra,
            'dec': phot.dec,
            'filter': phot.filter,
            'mjd': phot.mjd,
            'instrument_id': phot.instrument_id
        }

        filter = phot.packet['filter']
        magsys_packet = sncosmo.get_magsystem(phot.packet['magsys'])
        magsys_db = sncosmo.get_magsystem('ab')
        outsys = sncosmo.get_magsystem(outsys)

        relzp_out = 2.5 * np.log10(outsys.zpbandflux(filter))

        # note: these are not the actual zeropoints for magnitudes in the db or
        # packet, just ones that can be used to derive corrections when
        # compared to relzp_out
        relzp_packet = 2.5 * np.log10(magsys_packet.zpbandflux(filter))
        relzp_db = 2.5 * np.log10(magsys_db.zpbandflux(filter))

        packet_correction = relzp_out - relzp_packet
        db_correction = relzp_out - relzp_db

        # this is the zeropoint for fluxes in the database that is tied
        # to the new magnitude system
        corrected_db_zp = PHOT_ZP + db_correction

        if format == 'mag':
            if 'limiting_mag' in phot.packet:
                maglimit = phot.packet['limiting_mag']
                maglimit_out = maglimit + packet_correction
            else:
                # calculate the limiting mag
                fluxerr = phot.fluxerr
                fivesigma = 5 * fluxerr
                maglimit_out = -2.5 * np.log10(fivesigma) + corrected_db_zp

            retval.update({
                'mag': phot.mag + db_correction,
                'magerr': phot.e_mag,
                'magsys': 'ab',
                'limiting_mag': maglimit_out
            })
        elif format == 'flux':
            retval.update({
                'flux': phot.flux,
                'magsys': 'ab',
                'zp': corrected_db_zp,
                'fluxerr': phot.fluxerr
            })
        else:
            return self.error('Invalid output format specified. Must be one of '
                              f"['flux', 'mag'], got '{format}'.")

        return self.success(data=retval)

    @permissions(['Manage sources'])
    def put(self, photometry_id):
        """
        ---
        description: Update photometry
        parameters:
          - in: path
            name: photometry_id
            required: true
            schema:
              type: integer
        requestBody:
          content:
            application/json:
              schema:
                oneOf:
                  - $ref: "#/components/schemas/PhotometryMag"
                  - $ref: "#/components/schemas/PhotometryFlux"
        responses:
          200:
            content:
              application/json:
                schema: Success
          400:
            content:
              application/json:
                schema: Error
        """
        # Ensure user/token has access to parent source
        s = Source.get_if_owned_by(Photometry.query.get(photometry_id).obj_id,
                                   self.current_user)
        packet = self.get_json()

        try:
            phot = PhotometryFlux.load(packet)
        except ValidationError as e1:
            try:
                phot = PhotometryMag.load(packet)
            except ValidationError as e2:
                return self.error('Invalid input format: Tried to parse '
                                  f'{packet} as PhotometryFlux, got: '
                                  f'"{e1.normalized_messages()}." Tried '
                                  f'to parse {packet} as PhotometryMag, got:'
                                  f' "{e2.normalized_messages()}."')

        phot.id = photometry_id
        DBSession().merge(phot)
        DBSession().commit()
        return self.success()

    @permissions(['Manage sources'])
    def delete(self, photometry_id):
        """
        ---
        description: Delete photometry
        parameters:
          - in: path
            name: photometry_id
            required: true
            schema:
              type: integer
        responses:
          200:
            content:
              application/json:
                schema: Success
          400:
            content:
              application/json:
                schema: Error
        """
        # Ensure user/token has access to parent source
        s = Source.get_if_owned_by(Photometry.query.get(photometry_id).obj_id,
                                   self.current_user)
        DBSession.query(Photometry).filter(Photometry.id == int(photometry_id)).delete()
        DBSession().commit()

        return self.success()


PhotometryHandler.get.__doc__ = f"""
        ---
        description: Retrieve photometry
        parameters:
          - in: path
            name: photometry_id
            required: true
            schema:
              type: integer
          - in: query
            name: format
            required: false
            description: >-
              Return the photometry in flux or magnitude space?
              If a value for this query parameter is not provided, the
              result will be returned in magnitude space.
            schema:
              type: string
              enum:
                - mag
                - flux
          - in: query
            name: magsys
            required: false
            description: >- 
              The magnitude or zeropoint system of the output. (Default AB) 
            schema:
              type: string
              enum: {list(ALLOWED_MAGSYSTEMS)}
            
        responses:
          200:
            content:
              application/json:
                schema:
                  oneOf:
                    - $ref: "#/components/schemas/SinglePhotometryFlux"
                    - $ref: "#/components/schemas/SinglePhotometryMag"
          400:
            content:
              application/json:
                schema: Error
        """
