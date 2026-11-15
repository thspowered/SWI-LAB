-- Navrat vpred na v0.4 (CR-08) po rollbacku na v0.3.
--
-- Ziadosti, ktore medzitym podal kod v0.3, nemaju termin rozhodnutia.
-- Podla AC-09 je ich terminom starts_at - spravaju sa ako vo v0.2 a spatne
-- neprepadnu. Az potom sa da zapnut obmedzenie DD-9.
--
-- Pouzitie: zastavit aplikaciu v0.3, spustit tento skript, nasadit v0.4.
--   docker compose exec -T db psql -U swilab -d swilab -v ON_ERROR_STOP=1 < scripts/cr08_forward.sql
-- Bez tohto skriptu aplikacia v0.4 nenastartuje: kontrola schemy (ADR-02)
-- najde chybajuce obmedzenie.

BEGIN;

UPDATE reservations
   SET decision_deadline = starts_at
 WHERE state = 'PENDING_APPROVAL'
   AND decision_deadline IS NULL;

ALTER TABLE reservations
  ADD CONSTRAINT ck_reservations_request_has_deadline CHECK (
      state <> 'PENDING_APPROVAL'
      OR (decision_deadline IS NOT NULL AND decision_deadline <= starts_at)
  );

COMMIT;
