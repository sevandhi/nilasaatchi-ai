-- Helper functions used by the loader and the views (owner: gis-engineer).
-- geo_area_ha: geodesic (WGS84 spheroid) area in hectares of any geometry in any SRID; only the
-- polygonal part counts. Overlays are done in EPSG:32644 and measured with this (D-023).
CREATE OR REPLACE FUNCTION geo_area_ha(g geometry) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT coalesce(ST_Area(ST_Transform(ST_CollectionExtract(g, 3), 4326)::geography) / 1e4, 0)
$$;
