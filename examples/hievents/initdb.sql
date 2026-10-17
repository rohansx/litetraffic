-- Two separate databases on one Postgres so the vulnerable and fixed apps never
-- share schema or data: their migrations would otherwise collide. Runs once, when
-- the postgres data volume is first initialised.
CREATE DATABASE hievents_vuln;
CREATE DATABASE hievents_fixed;
