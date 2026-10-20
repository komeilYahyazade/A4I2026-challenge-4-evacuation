-- Operational tables the agent writes to. CREATE IF NOT EXISTS: re-running never truncates.
-- No row in any of these may describe a resident. Actor columns hold a staff role or service
-- account, never a member of the public.

-- The only thing kept from Google Maps is place_id. Status is re-grounded at runtime.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${OPS}.shelter_place_ids` (
  shelter_id STRING NOT NULL,
  place_id STRING,
  verified_at TIMESTAMP NOT NULL,
  verdict STRING OPTIONS (description = "KEEP | DROP | KEEP_UNVERIFIED")
)
OPTIONS (description = "Google Maps place_id per shelter. place_id only: no titles, URIs or text (Maps terms).");

-- Versioned plans. plan_json must have every Maps field stripped (maps_sources, supports,
-- live names, Maps text) before it is written.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${OPS}.plans` (
  plan_id STRING NOT NULL,
  county_fips STRING NOT NULL,
  version INT64 NOT NULL,
  mode STRING OPTIONS (description = "PLANNING | REHEARSAL"),
  created_at TIMESTAMP NOT NULL,
  created_by STRING,
  approved_by STRING,
  approved_at TIMESTAMP,
  status STRING OPTIONS (description = "DRAFT | APPROVED | SUPERSEDED"),
  plan_json JSON
)
OPTIONS (description = "Readiness plans, versioned. A person approves before publication.");

-- Reported, first-party shelter status. The input to rehearsal and response re-solves, which
-- never use Google Maps.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${OPS}.status_reports` (
  report_id STRING NOT NULL,
  shelter_id STRING NOT NULL,
  reported_at TIMESTAMP NOT NULL,
  status STRING OPTIONS (description = "OPEN | FULL | CLOSED | FLOODED"),
  occupancy INT64,
  source STRING OPTIONS (description = "Who reported it: county EOC, shelter manager role, rehearsal script")
)
OPTIONS (description = "First-party shelter status reports. Not from Google Maps.");

CREATE TABLE IF NOT EXISTS `${PROJECT}.${OPS}.decision_log` (
  ts TIMESTAMP NOT NULL,
  actor STRING,
  action STRING,
  detail JSON
)
OPTIONS (description = "Append-only log of agent and approver decisions.");

-- Field assessments: the record nobody wrote down, collected by asking.
CREATE TABLE IF NOT EXISTS `${PROJECT}.${OPS}.assessments` (
  assessment_id STRING NOT NULL,
  shelter_id STRING NOT NULL,
  assessed_at TIMESTAMP NOT NULL,
  assessor_role STRING,
  wheelchair_unaccompanied STRING OPTIONS (description = "YES | NO | UNRECORDED: can a wheelchair user get in alone, today"),
  notes STRING
)
OPTIONS (description = "Shelter accessibility assessments from operators and volunteers.");
