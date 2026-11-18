-- Navrat z v0.4 (CR-08) na kod v0.3 nad databazou s datami.
--
-- Postup z analyzy dopadu C08 (riadok Rollback) a z ADR-02. Rollback kodu
-- sam data nevrati: ziadost, ktorej uplynul termin rozhodnutia, je v
-- databaze zapisana ako PENDING_APPROVAL. Kod v0.3 by ju povazoval za zivu
-- az do starts_at - blokovala by a dala by sa schvalit. Ak medzitym niekto
-- na uvolneny termin potvrdil rezervaciu, databaza by podla v0.3 porusovala
-- BR-02.
--
-- Pouzitie: zastavit aplikaciu, spustit tento skript, nasadit kod v0.3.
--   docker compose exec -T db psql -U swilab -d swilab -v ON_ERROR_STOP=1 < scripts/cr08_rollback.sql
-- Aplikacia musi stat: ziadost, ktorej termin uplynie medzi krokom (1)
-- a nasadenim, by inak ostala zapisana ako ziva.

BEGIN;

-- (1) ziadostiam po termine rozhodnutia zapisat EXPIRED, kym to este ide
UPDATE reservations
   SET state = 'EXPIRED'
 WHERE state = 'PENDING_APPROVAL'
   AND decision_deadline <= now();

-- (2) obmedzenie DD-9 kod v0.3 nepozna - ziadosti, ktore vytvori, termin
-- nemaju a obmedzenie by ich odmietlo
ALTER TABLE reservations DROP CONSTRAINT ck_reservations_request_has_deadline;

COMMIT;

-- (3) nasadit kod v0.3. Stlpec decision_deadline ostava: kod v0.3 ho
-- nepozna a ignoruje ho, a navrat vpred (cr08_forward.sql) ho potrebuje.
