# Couzens rules

Every metric and every generated conclusion on the report must trace to a rule here.
Each rule has an ID. The code in `couzens.py` uses the same IDs, and the report shows them
next to each check. Thresholds live in `config.json` under `couzens`, so a change to a rule
is one edit and it shows up in git.

A rule marked **Couzens** is his published method. A rule marked **Adapted** is his method
applied to Apple Watch data, where his input (lactate, rMSSD, TrainingPeaks NP) is not
available. A rule marked **Plan** comes from the Nike Run Club 5K plan, not from Couzens.

## Decoupling and efficiency

**C1 Decoupling formula (Couzens).** Split a steady session into two equal halves.
Efficiency factor (EF) is output divided by heart rate in each half.
Decoupling = (EF first half − EF second half) / EF first half × 100.
Run output is power (Pw:HR) or speed (Pa:HR).
Source: [The Science of Decoupling, 2009](http://alancouzens.blogspot.com/2009/10/science-of-decoupling.html).
Code: `raw_decoupling` in `health_payload.py`. Both halves use only samples while moving, for
power, speed, *and* heart rate, so stops do not dilute the heart-rate mean.

**C2 The ±5% line (Couzens).** About 5% is "fatigue at a safe level". A swing in either
direction can mean tiredness: a 5% fall in output at the same heart rate, *or* a 5% fall in
heart rate at the same output (over-reaching studies). Flag |decoupling| > 5.
Source: same essay and his replies in its comments.
Config: `couzens.decoupling_limit`.

**C3 Steady sessions only (Couzens).** Decoupling describes a steady aerobic session.
It does not describe intervals. Only runs of at least `filters.min_steady_min` minutes,
with pace variation ≤ `filters.steady_cv`, and halves whose output matches within
`filters.half_match`, are scored. Runs of 40 minutes or more carry the durability check.

**C4 Compare EF like for like (Couzens).** EF changes are only meaningful between efforts
that are alike: same heart-rate band, similar conditions. The report tracks EF inside the
easy heart-rate band, never across all runs mixed.
Source: [Kona conditions](https://alancouzens.com/blog/kona_conditions.html) (EF compared to
the month of home training).

**C5 Heat lowers EF (Couzens).** Heat and fluid loss raise heart rate at the same output.
His Kona crew lost about 17% EF overall and about 20% on the run. A hot easy run is not a
fitness loss. The report splits easy runs by workout temperature before reading EF.
Sources: [Science of Decoupling](http://alancouzens.blogspot.com/2009/10/science-of-decoupling.html),
[Kona conditions](https://alancouzens.com/blog/kona_conditions.html).
Config: `couzens.hot_c`, `couzens.cool_c`.

## Intensity and the aerobic threshold

**C6 Easy is at or under the aerobic threshold (Couzens).** Without a lab, start the week
mostly easy (AeT −10 bpm to AeT) and add steady work (AeT to AeT +10 bpm) as tolerated.
AeT is the first rise in lactate. This export has no lactate.
Source: [Endurance Physiology 101](https://alancouzens.com/blog/endurance_physiology_101.html),
[Zones and AeT](https://alancouzens.com/blog/zone_qu.html).
**Adapted:** the AeT estimate is the median heart rate of steady runs of 40 minutes or more
that stay inside ±5% decoupling (C2). It is a candidate, not a lab value. The easy band is
`zones.easy_hr` until that candidate exists.

**C7 Volume and intensity are two loads (Couzens).** One total (TSS) hides a long easy week
and a short hard week. Track easy time and hard time separately.
Source: [Volume v Intensity Responders](https://www.alancouzens.com/blog/vol_int_responder.html).
**Adapted:** a workout with mean heart rate ≥ `zones.hard_bpm` is hard time. The weighted
"stress" line multiplies hard minutes by `zones.hard_weight`. It is not TSS.

**C8 Monthly volume drives aerobic fitness (Couzens).** In his 22,000 files, plain monthly
hours predicted fitness better than intensity. Volume is the base dose.
Source: [Volume vs Intensity](https://www.alancouzens.com/blog/volume-vs-intensity.php).

**C9 Shape of a base week (Couzens).** A bulk of easy-steady aerobic work. One longer
session each week, about 1.5× the average session. An up-tempo effort of 5–8% of the week.
A solid effort under 5%, at least every other week. Regular fast reps under 3%.
Source: [Destructing your Annual Training Plan, Part I](http://alancouzens.blogspot.com/2009/10/destructing-your-annual-training-plan.html).
Config: `couzens.long_run_ratio`, `couzens.easy_share_min`.

**C10 Keep the top end small in base (Couzens).** No more than about 5% of weekly time at
or above the 4 mmol/L mark while building the base.
Source: [Performance Pyramid III: Speed/Power](http://alancouzens.blogspot.com/2013/08/building-your-performance-period-iii.html).

## Speed phase

**C11 Speed intensity (Couzens).** Speed work is 90–100% of VO2max, which is 3K to 10K pace.
**Adapted:** 3K and 10K pace come from the best recorded 5 km with the Riegel exponent 1.06
(3K pace = 5K pace × 0.6^0.06, 10K pace = 5K pace × 2^0.06). This is the pace you hold
*now*, not the goal pace.
Source: [Real World Periodization IV: The Need For Speed, 2008](http://alancouzens.blogspot.com/2008/09/real-world-periodization-iv-need-for.html).

**C12 Speed frequency (Couzens).** Two sessions a week are enough when VO2max is under
40 ml/kg/min. Three to four are needed above 50.
Source: same post. Config: `couzens.vo2_two_speed_days_below`.

**C13 Hold volume in a speed phase (Couzens).** Keep total volume at 66–80% of the recent
maximum. Larger cuts lose the slow peripheral adaptations.
Source: same post. Config: `couzens.speed_volume_floor`, `couzens.speed_volume_ceiling`,
`couzens.peak_lookback_weeks`.
**Adapted:** the maximum is the highest 4-week mean of weekly run hours in the
`peak_lookback_weeks` before the plan starts.

**C14 Sharpening goes near the event (Couzens).** VO2max work has most effect 2–10 weeks
before the target event and fades quickly afterwards.
Source: [Periodization: Don't put the cart before the horse](https://www.alancouzens.com/blog/periodization.html).

## Readiness and recovery

**C15 Known-pace heart-rate check (Couzens).** If heart rate at a known easy pace is 5%
above normal, recovery is incomplete. Postpone the key session. A 5% fall is also a warning.
Source: [Science of Decoupling](http://alancouzens.blogspot.com/2009/10/science-of-decoupling.html).
Config: `couzens.readiness_band`, `couzens.known_pace`, `couzens.baseline_days`.

**C16 HRV sets the day (Couzens).** Against the athlete's own history:
top 25% → intensity day; middle 50% → aerobic day; bottom 25% → recovery;
bottom 3% → rest.
Source: [Overtraining: Using HRV effectively](https://alancouzens.com/blog/overtraining_HRV.html).
**Adapted:** Apple stores SDNN, not morning rMSSD. The quartiles use the daily SDNN median
over `couzens.hrv_window_days`. Treat it as a weaker signal than his rMSSD.

**C17 Readiness combines markers (Couzens).** He combines HRV, resting heart rate, sleep,
mood, soreness, fatigue, and life stress, against the athlete's own baseline, and for the
session planned. Low readiness → recovery. High readiness → the key session.
Source: [Ready, set, go?](https://alancouzens.com/TP/athletes.cgi/blog/readiness),
[Learning to code 5](https://www.alancouzens.com/blog/Learning_to_code_5.html).
**Adapted:** resting heart rate more than `readiness_band`% above its 28-day median is a
flag. Sleep under `couzens.sleep_target_h` is a flag. A journal score of 2 or less (1 poor,
5 great) for mood, soreness, fatigue, or stress is a flag. Each flag lowers the HRV level by
one step. Two or more flags give recovery at most.

**C18 Adaptation needs recovery (Couzens).** Get tired (~5%), recover, then come back at a
higher level. That cycle is the test of adaptation, not fatigue alone.
Source: his replies under The Science of Decoupling.

## Plan rules (not Couzens)

**P1 Week shape.** Two speed runs, two easy runs, and one long run each week.
**P2 5K pace means today's 5K.** In the plan, "5K pace" is the pace you can hold now.
**P3 Best pace is the small part.** Goal-pace and faster reps stay a short set
(`plan.goal_pace_cap_min`, `plan.fastest_cap_min`).

## Measurement rules (data hygiene)

**M1 One session, one record.** When a non-Watch workout overlaps an Apple Watch workout by
at least half of the shorter one, keep the Watch workout. Drop exact duplicates.
**M2 Workout statistics.** Use the statistic row that spans the whole workout. If there is no
such row, sum the lap rows (distance, energy) or take the duration-weighted mean (averages).
**M3 Night of sleep.** A sleep segment belongs to the morning 12 hours after it starts.
A segment that starts between 11:00 and 18:00 is a nap and is not part of the night.
**M4 Plausible samples.** Heart rate, power, and speed outside the `filters` limits are
removed. The limits allow sprint reps; they only cut sensor faults.
**M5 Deterministic output.** The same export, config, and journal give the same report.
The "as of" date is the newest record in the export, never the clock.
