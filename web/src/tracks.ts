// Mirror of agent/readiness_agent/brief_plan.py TRACKS: what each brief track looks at.
import type { Track } from "./types";

export const TRACKS: Record<Track, { label: string; question: string; who: string; checks: string }> = {
  shelter_readiness: {
    label: "Shelter readiness",
    question: "Can the shelters near the least mobile neighbourhoods really take wheelchair users?",
    who: "Tracts ranked by need score (no-car households, 65+, disability, mobile homes, group quarters), then nursing homes with no generator shelter in reach.",
    checks: "The nearest pre-landfall shelters to each top tract, one building at a time on Google Maps.",
  },
  getting_people_out: {
    label: "Getting people out",
    question: "Who needs a ride, from where, to where, and how long is the run?",
    who: "Tracts ranked by households without a car; each marked walkable or pickup needed.",
    checks: "The nearest pre-landfall shelter for each top tract, with a Google Maps drive time, plus who is left without a seat within 15 km.",
  },
  medically_dependent: {
    label: "Medically dependent",
    question: "Where do nursing-home and power-dependent residents go, and is there a generator there?",
    who: "Nursing homes (beds, distance to a shelter with a recorded generator) and the county's power-dependent total, then the top-need tracts.",
    checks: "Medical-needs and generator-recorded shelters nearest each nursing home, with a drive time from the facility.",
  },
};
