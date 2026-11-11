#!/usr/bin/env python3
"""Stage 98: rerun D(c) and E-no-HPR components, search fixed weight combinations.

Same Stage97 source dates: 2026-10-01 through 2026-11-10, identical
archived 09:45 UTC pre-race snapshot eligibility. PREDICT requires sealed
results and pre-2026-10-01 historical DB. Feature vectors for every declared
horse are SHA-256 sealed BEFORE outcomes are restored. SETTLE ranks *all*
pre-race runners under each candidate weight profile and evaluates against
subsequently revealed complete results. Models are never updated live.

Separate historical exploration from chronological late-date evaluation:
train 2026-10-01..2026-11-05, heldout 2026-11-06..2026-11-10.
Dates with no verified preoff snapshot are excluded, not invented. Model
recipes were researched retrospectively, so this is not a genuine untouched
prospective test even though the race outcomes are embargoed during scoring.
"""
from __future__ import annotations
import argparse,collections,csv,datetime as dt,gzip,hashlib,json,math,pathlib,re,sqlite3,statistics,subprocess,sys
import numpy as np
import stage97_nh_blind_oct2026 as S97
import stage95_five_model_paired_replay as S95
import stage94_dc_probability_replay as S94
import stage30_gap_aware_abcd as OLD
import stage38_model_dz_live as D
import stage65p_race_type_c_only as C
import stage92_model_e_winprob as E
import stage86_hpr_engine as H

VERSION='stage98-nh-reweight-chronological-v1'
START,END,CUTOFF='2026-10-01','2026-11-10','2026-10-01'
TRAIN_END='2026-11-05'
HOLD_START='2026-11-06'
MODEL_D='D_C'
MODEL_E='E_75_25_NO_HPR'
MODELS=(MODEL_D,MODEL_E)
PILLARS=E.PILLARS
SUBTYPES=('HURDLE','CHASE','NH_FLAT')
C_SHARES=(0.,.10,.20,.25,.35,.50,.65)
D_PART_SHARES=(.35,.50,.65,.80,1.)
BASE_E_PROFILES=('people_heavy','balanced','ability_heavy','conditions_heavy','course_distance','ground_jump','frozen_d_prior')
BOOST_PILLARS=('ability','trainer','jockey','course','distance','ground','jumping')
E_PROFILES=BASE_E_PROFILES+tuple('BOOST_'+p.upper() for p in BOOST_PILLARS)
D_PROFILES=('FROZEN_D',)+E_PROFILES
OBJECTIVES=('STRIKE_RATE','ROI','BALANCED')
MIN_TRAIN_TYPE=12
MIN_TRAIN_DISC=20
MIN_TRAIN_GLOBAL=40
MIN_HOLD_TYPE=3
BASIC_FIELDS=('date','course','time_utc','snapshot_sha','race_id','race_name','discipline','race_type','declared_field')
RECOVERED_PRICES=(('2026-10-18', 'Limerick', '13:30', 'A Boy The Kid', '1', 1, '3/1', 4.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '13:00', 'Brickfield Belle', '2', 0, '4/7', 1.5714285714285714, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '16:00', 'Future Prospect', '2', 0, '3/1', 4.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '14:30', 'Giorgia Rose', 'PU', 0, '28/1', 29.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Limerick', '13:30', 'Global Submission', '2', 0, '11/10', 2.1, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '15:30', 'Hashtag Pretender', '4', 0, '7/2', 4.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '13:00', 'Kimi de Mai', '1', 1, '13/8', 2.625, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '14:30', 'Lucy Wang', '6', 0, '17/2', 9.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '13:30', 'Northern Silence', '3', 0, '22/1', 23.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '14:30', 'Sallap', '5', 0, '11/2', 6.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Limerick', '16:00', 'Straight Home', '4', 0, '18/1', 19.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Limerick', '15:30', 'Zurich', '5', 0, '3/1', 4.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/limerick/'), ('2026-10-18', 'Sedgefield', '14:50', 'Asa', 'F', 0, '25/1', 26.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.oddschecker.com/horse-racing/results/2026-10-18/sedgefield/15%3A50'), ('2026-10-18', 'Sedgefield', '13:50', "Can't Resist It", '4', 0, '5/2', 3.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '14:20', 'Dexperts Opinion', '2', 0, '13/8', 2.625, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '15:20', 'Double Digits', '3', 0, '3/1', 4.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '12:50', 'Edelak', '2', 0, '4/5', 1.8, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '15:20', 'Gambino', '9', 0, '2/1', 3.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '15:50', 'Going On', '5', 0, '6/4', 2.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '13:50', 'Hay Max', '1', 1, '13/2', 7.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '13:50', 'Lady Boujee', '6', 0, '11/2', 6.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '14:50', 'Miss Friday Lions', 'PU', 0, '13/2', 7.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.oddschecker.com/horse-racing/results/2026-10-18/sedgefield/15%3A50'), ('2026-10-18', 'Sedgefield', '15:50', 'Mysterland', '1', 1, '2/1', 3.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '15:50', 'Parkside Lily', '3', 0, '9/2', 5.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '14:20', 'Passengerontheship', '1', 1, '9/2', 5.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '14:20', 'Red Delta', '4', 0, '15/2', 8.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '15:20', 'Rewired', '8', 0, '28/1', 29.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '13:20', 'Royal Time', '1', 1, '7/1', 8.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-18/sedgefield/'), ('2026-10-18', 'Sedgefield', '12:50', 'Tommy Pickles', '1', 1, '10/11', 1.9090909090909092, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-18', 'Sedgefield', '14:50', 'Zamond', '4', 0, '2/1', 3.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '13:00', 'Bankatary', '4', 0, '14/1', 15.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '13:00', 'Bass Hunter', '1', 1, '1/4', 1.25, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '15:30', 'Bohemian Breeze', '5', 0, '3/1', 4.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '16:05', 'Burning Embers', '2', 0, '6/1', 7.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '14:00', 'Expert Analysis', '3', 0, '10/3', 4.333333333333334, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-19/plumpton/'), ('2026-10-19', 'Plumpton', '14:00', 'Golly Gosh', '1', 1, '4/9', 1.4444444444444444, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-19/plumpton/'), ('2026-10-19', 'Plumpton', '13:30', 'Gone Country', '1', 1, '7/4', 2.75, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '16:05', 'Grey Gamble', '5', 0, '7/1', 8.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '15:00', 'Jorebel', '3', 0, '7/2', 4.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '15:30', 'Latin', '2', 0, '9/4', 3.25, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '13:30', 'Market House', '4', 0, '3/1', 4.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '13:00', 'Marnier', '3', 0, '16/1', 17.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-19/plumpton/'), ('2026-10-19', 'Plumpton', '15:00', 'Palamon', '2', 0, '2/1', 3.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '14:30', 'Sixty Plus', '2', 0, '3/1', 4.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-19/plumpton/'), ('2026-10-19', 'Plumpton', '15:00', 'Tapley', '1', 1, '5/2', 3.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-19', 'Plumpton', '14:30', 'Tashan', '5', 0, '4/1', 5.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-19/plumpton/'), ('2026-10-19', 'Plumpton', '13:30', 'Tipu Sultan', '6', 0, '80/1', 81.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '14:42', 'Blakey Boy', '4', 0, '9/4', 3.25, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '14:42', 'Coded Welder', '3', 0, '11/8', 2.375, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '14:07', "Codger's Rock", '2', 0, '3/1', 4.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '14:07', 'Conquer The Breeze', '6', 0, '9/2', 5.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '15:50', 'Defence Witness', '7', 0, '17/2', 9.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-20/hexham/'), ('2026-10-20', 'Hexham', '14:42', 'Do No Wrong', '5', 0, '15/2', 8.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '14:07', 'Forest Blaze', '5', 0, '10/1', 11.0, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '15:17', 'Jeriko du Reponet', '2', 0, '7/2', 4.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '13:32', 'Littletown Lad', '1', 1, '7/4', 2.75, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '15:17', 'Royal Infantry', '1', 1, '4/11', 1.3636363636363638, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '13:32', 'Snapaudaciaheros', '3', 0, '8/1', 9.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-20/hexham/'), ('2026-10-20', 'Hexham', '15:50', 'Super Citizen', '3', 0, '15/2', 8.5, 'VERIFIED_PINNED_KAGGLE_V129', 'https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025'), ('2026-10-20', 'Hexham', '13:01', 'Tralee Girl', '1', 1, '6/4', 2.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-20/hexham/'), ('2026-10-20', 'Hexham', '13:32', 'Walk On Son', '4', 0, '14/1', 15.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-20/hexham/'), ('2026-10-31', 'Ascot', '15:10', 'Babs', '1', 1, '11/4', 3.75, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '14:33', 'Califet En Vol', '6', 0, '5/2', 3.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '16:15', "Colombe d'Or", '3', 0, '7/2', 4.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '13:25', 'Dance And Glance', '3', 0, '7/2', 4.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '15:43', 'Fasol', '4', 0, '12/1', 13.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '13:25', 'Glynn Brae', '5', 0, '9/2', 5.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '14:33', 'Imperial Alex', '9', 0, '33/1', 34.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '13:25', 'Merely A Detail', '4', 0, '15/2', 8.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '15:43', 'Morning Mayhem', '7', 0, '12/1', 13.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '15:10', 'No Way Jay', '2', 0, '7/4', 2.75, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '16:15', 'Saint Argente', '1', 1, '2/1', 3.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '16:15', "Shane's Spirit", '6', 0, '9/1', 10.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '15:10', 'Sportingsilvermine', '3', 0, '13/8', 2.625, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '15:43', 'Trustintimes', '1', 1, '11/4', 3.75, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ascot', '14:33', 'Viroflay', '1', 1, '3/1', 4.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ascot/'), ('2026-10-31', 'Ayr', '12:43', 'Battle of Arsuf', '6', 0, '12/1', 13.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '16:10', 'Better Be Definite', '12', 0, '10/1', 11.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '13:53', 'Blue Las', '4', 0, '13/2', 7.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '16:10', 'Bollin Matilda', '11', 0, '5/1', 6.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '14:28', 'Halftheworldaway', '8', 0, '7/2', 4.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '13:53', 'Knomorediamonds', '5', 0, '3/1', 4.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '12:43', 'Majordomo', '4', 0, '5/2', 3.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '16:10', 'Melbourne Ghost', '6', 0, '20/1', 21.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '14:28', 'My Kiwi Girl', '2', 0, '33/1', 34.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '16:10', 'Reagrove Lord', '7', 0, '8/1', 9.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '13:53', 'Serious Chat', '2', 0, '9/1', 10.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '12:43', 'The Skecher', '1', 1, '10/11', 1.9090909090909092, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Ayr', '14:28', 'Wolfburg', '9', 0, '9/1', 10.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://stablewhispers.co.uk/fast-results/2026-10-31/ayr/'), ('2026-10-31', 'Wetherby', '15:30', 'Filibustering', '1', 1, '1/1', 2.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '13:10', 'Kahavari', '6', 0, '6/1', 7.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '15:30', 'Klitschko', '2', 0, '4/1', 5.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '13:10', 'Koukeo', '2', 0, '9/2', 5.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '13:10', 'Ladronne', '5', 0, '14/1', 15.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '12:35', 'Loopy Mag', '3', 0, '10/1', 11.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '12:35', 'Minella Hollow', '1', 1, '2/7', 1.2857142857142856, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '16:03', "Paddy O'Mahler", '4', 0, '10/3', 4.333333333333334, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '13:10', 'Palamon', '3', 0, '2/1', 3.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '14:55', "Pic D'orhy", '1', 1, '10/11', 1.9090909090909092, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '14:55', 'Riskintheground', '3', 0, '28/1', 29.0, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '16:03', 'Sledgehammer', '1', 1, '4/5', 1.8, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '14:55', 'Twinjets', 'PU', 0, '13/2', 7.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/'), ('2026-10-31', 'Wetherby', '16:03', 'Upfordebate', '3', 0, '11/2', 6.5, 'ONLINE_RESULTS_OFFICIAL_SP', 'https://www.horseracingbetting.co.uk/results/2026-10-31/')) # 103 source-verified Stage97 recovery rows


def norm(x):return S97.norm(x)
def venue(x):return S97.venue(x)
def number(x):return S97.num(x)
def z(x):return np.asarray(S95.standardized(np.asarray(x,dtype=float)))


def profiles(original_d_weights):
    base=dict(zip(PILLARS,[float(v) for v in original_d_weights]))
    out={'FROZEN_D':base,**{p:dict(E.PRESETS[p]) for p in BASE_E_PROFILES}}
    for pillar in BOOST_PILLARS:
        w=dict(E.PRESETS['people_heavy']);w[pillar]*=1.8
        out['BOOST_'+pillar.upper()]=w
    return out


def nh_weights(profile_name,disc,profile_map):
    w={k:float(profile_map[profile_name][k]) for k in PILLARS}
    w['draw']=0.
    if disc=='NH_FLAT':w['jumping']=0.
    if disc in ('HURDLE','CHASE'):w['ground']*=1.4 if profile_name!='FROZEN_D' else 1.
    tot=sum(w.values())
    if tot<=0:raise ValueError('Empty pillar weights')
    return np.asarray([w[k]/tot for k in PILLARS])


def candidates():
    res=[]
    for profile in D_PROFILES:
        for c in C_SHARES:
            res.append(dict(id=f'DC__{profile}__C{int(round(c*100)):02d}',model=MODEL_D,
                            d_profile=profile,c_share=c,d_evidence_share=None))
    for profile in E_PROFILES:
        for d in D_PART_SHARES:
            for c in C_SHARES:
                res.append(dict(id=f'E__{profile}__D{int(round(d*100)):02d}__C{int(round(c*100)):02d}',
                                model=MODEL_E,d_profile=profile,c_share=c,d_evidence_share=d))
    assert len(res)==len({x['id'] for x in res})
    return res


def baseline_ids():
    return {MODEL_D:'DC__FROZEN_D__C25',MODEL_E:'E__people_heavy__D65__C25'}


def score_race(race,choice,profile_map):
    raw=np.asarray(race['d_components'],dtype=float)
    cp=np.asarray(race['c_probability'],dtype=float)
    assert raw.ndim==2 and raw.shape[1]==8 and len(cp)==raw.shape[0]
    c_share=float(choice['c_share'])
    if choice['model']==MODEL_D:
        weights=nh_weights(choice['d_profile'],race['discipline'],profile_map)
        d_score=raw@weights
        return z(d_score)*(1-c_share)+z(cp)*c_share
    if choice['model']==MODEL_E:
        # Exactly Stage95 E no-old-HPR: within-field-normalised D pillars,
        # direct contemporary E pillars, blend then weighted eight pillars.
        d=(np.clip(50+12*(raw-raw.mean(axis=0))/(raw.std(axis=0)+1e-8),15,85))
        contemporary=np.asarray(race['contemporary_components'],dtype=float)
        assert contemporary.shape==d.shape
        d_share=float(choice['d_evidence_share'])
        w=nh_weights(choice['d_profile'],race['discipline'],profile_map)
        e=((d_share*d+(1-d_share)*contemporary)@w)
        return z(e)*(1-c_share)+z(cp)*c_share
    raise ValueError('Unexpected model')


def ranks(race,variant,weights):
    scored=score_race(race,variant,weights)
    return sorted(range(len(scored)),key=lambda i:(-float(scored[i]),norm(race['horses'][i])))


def write_csv(path,rows,fields=None):
    path=pathlib.Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fields=fields or (list(rows[0]) if rows else ['none'])
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)


def read_features(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:return [json.loads(s) for s in f]


def verified_recovered_prices():
    # No names-only join; date, course, UTC off, horse, finish and win must agree.
    out={}
    for d,course,off,horse,finish,won,sp,dec,source,proof in RECOVERED_PRICES:
        key=(d,venue(course),off,norm(horse))
        if key in out and abs(out[key]['decimal']-float(dec))>1e-7:
            raise RuntimeError('Conflicting recovered price '+str(key))
        if source not in ('ONLINE_RESULTS_OFFICIAL_SP','VERIFIED_PINNED_KAGGLE_V129'):
            raise RuntimeError('Unsupported SP evidence')
        out[key]=dict(decimal=float(dec),sp=sp,source=proof,finish=finish,won=int(won))
    return out


def predict(args):
    out=pathlib.Path(args.out);out.mkdir(parents=True,exist_ok=True)
    root=pathlib.Path(args.root);db=pathlib.Path(args.db);ratings=pathlib.Path(args.ratings)
    if (root/'data/racing-results').exists() or (root/'data/race-results.json').exists():
        raise RuntimeError('Post-cutoff results visible before prediction')
    with sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True) as con:
        assert con.execute('select count(*) from historical_races where race_date>=?',(CUTOFF,)).fetchone()[0]==0
    (past,form,(scaler,clf),weights,people,draw,hr),state=S97.prepare_model_state(root,db,ratings)
    profiles_map=profiles(weights)
    snapshots,meta,coverage=S97.load_snapshots(root,START,END)
    totals=collections.Counter();errors=collections.Counter();records=[]
    fixed={r['id']:r for r in candidates() if r['id'] in baseline_ids().values()}
    try:
        for date in sorted(snapshots):
            eligible,reject=S97.model_groups(date,snapshots[date]);errors.update(reject)
            for key,original_field in eligible:
                field=[{**r,'form':''} for r in original_field]
                unique={}
                for h,country in past.last:
                    if h not in unique:unique[h]=country
                    elif unique[h]!=country:unique[h]=None
                try:
                    live=[D.live_row(r,date,'STAGE97|'+key,unique) for r in field]
                    scored=past.score(live,live=True)
                    if scored is None:
                        errors['UNSCORABLE_FROZEN_D']+=1;continue
                    x=np.asarray(scored['x'][:,:8],dtype=float)
                    ors=[C.num(r.get('official_rating') or r.get('or') or r.get('ofr')) for r in field]
                    lbs=[C.num(r.get('weight_lbs')) for r in field]
                    avg_or=statistics.mean(v for v in ors if v is not None) if any(v is not None for v in ors) else None
                    avg_lbs=statistics.mean(v for v in lbs if v is not None) if any(v is not None for v in lbs) else None
                    feats=[]
                    for r in field:
                        item={'date':date,'horse':r.get('horse'),'country':r.get('horse_country'),
                            'trainer':r.get('trainer'),'jockey':r.get('jockey'),
                            'race_type':r.get('race_name') or r.get('race_type') or r.get('type'),
                            'surface':r.get('surface'),'race_class':r.get('race_class'),
                            'or':r.get('official_rating') or r.get('or') or r.get('ofr'),
                            'weight':r.get('weight_lbs')}
                        feats.append(form.vector(item,avg_or,avg_lbs)[0])
                    cprob=clf.predict_proba(scaler.transform(np.asarray(feats)))[:,1]
                    extra=np.asarray([[E.condition_pillars(r,[],field,people[0],people[1],draw)['pillars'][k]
                                       for k in PILLARS] for r in field],dtype=float)
                    first=field[0];disc=S97.code(first.get('type') or first.get('race_type') or first.get('race_name'))
                    race={
                        'date':date,'course':first['course'],'time_utc':key.rsplit('|',1)[-1],
                        'snapshot_sha':meta[date][0],'race_id':str(first.get('race_id') or ''),
                        'race_name':str(first.get('race_name') or ''),'discipline':disc,
                        'race_type':S94.race_cat(first),'declared_field':len(field),
                        'horses':[r['horse'] for r in field],
                        'd_components':x.round(12).tolist(),
                        'contemporary_components':extra.round(12).tolist(),
                        'c_probability':np.asarray(cprob,dtype=float).round(12).tolist(),
                    }
                    assert all(np.isfinite(a).all() for a in (x,extra,cprob))
                    records.append(race);totals[date]+=1
                except (ValueError,ArithmeticError) as exc:
                    errors['SCORE_NUMERIC_ERROR']+=1
                    print('Stage98 rejected:',date,key,type(exc).__name__,flush=True)
            print('Stage98 blind feature reconstruction',date,totals[date],flush=True)
    finally:hr.close()
    if len(records)<40:raise RuntimeError('Insufficient sealed feature vectors')
    # Read-only all-runner inputs, but no outcomes have been opened here.
    feature_path=out/'stage98-blind-all-runner-features.jsonl.gz'
    with gzip.open(feature_path,'wt',encoding='utf-8') as dest:
        for row in records:dest.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n')
    h=hashlib.sha256(feature_path.read_bytes()).hexdigest()
    baseline=[]
    for row in records:
        for model,id in baseline_ids().items():
            ids=ranks(row,fixed[id],profiles_map)
            for rank,i in enumerate(ids[:2],1):
                baseline.append(dict(**{k:row[k] for k in BASIC_FIELDS},model=model,rank=rank,
                    horse=row['horses'][i]))
    write_csv(out/'stage98-baseline-unsettled.csv',baseline)
    comparison={'stage':98,'version':VERSION,'period':[START,END],'cutoff_exclusive':CUTOFF,
       'trained_on_dates_through':TRAIN_END,'heldout_from':HOLD_START,
       'information_blind':True,'no_outcomes_visible_at_prediction':True,
       'nh_races':len(records),'by_date':dict(totals),'scorer_rejections':dict(errors),
       'features_sha256':h,'original_D_weights':weights,'frozen_history_state':state,
       'candidate_count':len(candidates()),'candidate_models':list(MODELS),
       'source':'Stage97 exact preoff cards','result_source_not_opened':True,
       'no_live_model_changes':True}
    (out/'stage98-blind-manifest.json').write_text(json.dumps(comparison,indent=2)+'\n')
    print(json.dumps({'stage':98,'phase':'PREDICT','races':len(records),'features_sha256':h},indent=2),flush=True)


def verify_baseline(out, reference):
    # Stage97 reproducibility is a HARD gate, not a soft assertion.
    reference=pathlib.Path(reference)
    m=json.loads((reference/'stage97-blind-manifest.json').read_text())
    zipped=reference/'stage97-blind-rank1-rank2.csv.gz'
    if hashlib.sha256(zipped.read_bytes()).hexdigest()!=m['predictions_sha256']:
        raise RuntimeError('Stage97 source manifest checksum disagrees')
    if m['nh_races']!=218:raise RuntimeError('Unexpected Stage97 original race count')
    with gzip.open(zipped,'rt',encoding='utf-8',newline='') as f:
        base={ (r['date'],venue(r['course']),r['time_utc'],r['model'],int(r['rank'])):norm(r['horse'])
                for r in csv.DictReader(f) if r['model'] in MODELS}
    with (out/'stage98-baseline-unsettled.csv').open(encoding='utf-8',newline='') as f:
        rec={ (r['date'],venue(r['course']),r['time_utc'],r['model'],int(r['rank'])):norm(r['horse'])
              for r in csv.DictReader(f)}
    missing=list(set(base)^set(rec))
    changed=[k for k in base.keys()&rec.keys() if base[k]!=rec[k]]
    result=dict(stage97_races=m['nh_races'],stage98_rank12_records=len(rec),stage97_rank12_records=len(base),
                mismatched_keys=missing[:20],changed_picks=changed[:20],same_rank1_rank2=(not missing and not changed))
    (out/'stage98-reproducibility.json').write_text(json.dumps(result,indent=2)+'\n')
    if missing or changed:
        raise RuntimeError(f'Frozen scoring not reproduced: {len(missing)} keys / {len(changed)} picks changed')
    return result


def attach_results(out,root):
    manifest=json.loads((out/'stage98-blind-manifest.json').read_text())
    feature_path=out/'stage98-blind-all-runner-features.jsonl.gz'
    if hashlib.sha256(feature_path.read_bytes()).hexdigest()!=manifest['features_sha256']:
        raise RuntimeError('Sealed features have been modified')
    allr=read_features(feature_path)
    if len(allr)!=manifest['nh_races']:raise RuntimeError('Sealed race count differs')
    results,rejected=H.load_results(root,dt.date.fromisoformat(START),dt.date.fromisoformat(END))
    prices=S94.price_candidates(root,dt.date.fromisoformat(START),dt.date.fromisoformat(END))
    recovered=verified_recovered_prices()
    status=collections.Counter();outcomes=[];missing_prices=[];used_recovered=set();conflicts=[]
    for race in allr:
        key=f"{race['date']}|{venue(race['course'])}|{race['time_utc']}"
        r=results.get(key)
        # Require full actual starter field verified against sealed (declared) field.
        actual={norm(x.get('horse')):x for x in r['runners']} if r else {}
        declared={norm(s) for s in race['horses']}
        winner=sum(H.win_status(x) for x in actual.values())
        matching=bool(r and len(actual)>=2 and len(actual)<=len(declared) and
                      set(actual)<=declared and len(declared)==len(race['horses']) and winner==1)
        if r and race['race_id'] and r.get('race_id') and race['race_id']!=str(r['race_id']):matching=False
        if not matching:
            why='UNVERIFIED_RACE_OR_RESULT' if r else 'MISSING_RESULT'
            status[why]+=1
            outcomes.append({**race,'verified':False,'settlement':[],'result_status':why})
            continue
        status['VERIFIED_RACE']+=1
        payouts=[]
        for horse in race['horses']:
            h=norm(horse)
            rr=actual.get(h)
            if rr is None:
                payouts.append(dict(horse=horse,started=False,won=None,finish='NR',sp=None,sp_decimal=None,sp_source=''))
                continue
            pos,won=S94.parse_finish(rr)
            if pos=='NR':
                payouts.append(dict(horse=horse,started=False,won=None,finish='NR',sp=None,sp_decimal=None,sp_source=''))
                continue
            sp,dec,source=S94.choose_price(prices,key,horse)
            fixed=recovered.get((race['date'],venue(race['course']),race['time_utc'],h))
            if fixed:
                if bool(fixed['won'])!=bool(won):
                    conflicts.append([key,horse,'WINNER_MISMATCH']);fixed=None
                elif str(pos).upper().strip()!=str(fixed['finish']).upper().strip():
                    conflicts.append([key,horse,'FINISH_MISMATCH']);fixed=None
                elif dec is not None and abs(dec-fixed['decimal'])>0.001:
                    conflicts.append([key,horse,'SP_DISAGREEMENT']);fixed=None
            if dec is None and fixed:
                sp,dec,source=fixed['sp'],fixed['decimal'],fixed['source'];used_recovered.add((key,h))
            if dec is None:
                missing_prices.append(dict(date=race['date'],course=race['course'],time_utc=race['time_utc'],
                    horse=horse,finish=pos,won=int(won),race_type=race['race_type'],reason='NO_VERIFIED_SP'))
            payouts.append(dict(horse=horse,started=True,won=int(won),finish=pos,
                sp=sp,sp_decimal=dec,sp_source=source))
        outcomes.append({**race,'verified':True,'settlement':payouts,'result_status':'VERIFIED_RACE'})
    if conflicts:
        raise RuntimeError('Conflicting SP audit evidence: '+str(conflicts[:6]))
    write_csv(out/'stage98-missing-runner-sps.csv',missing_prices,
              ['date','course','time_utc','horse','finish','won','race_type','reason'])
    return outcomes,dict(status),dict(source_load_exclusions=rejected,recovered_unique_used=len(used_recovered),
                        new_missing_runner_sp=len(missing_prices))


def stats(rr):
    # rr are single-rank selections, including nonrunners/unknown results.
    n=len(rr);started=[r for r in rr if r.get('started') is True]
    priced=[r for r in started if number(r.get('sp_decimal')) is not None]
    winners=sum(int(r['won']) for r in started)
    profit=sum(float(r['sp_decimal'])-1 if r['won'] else -1 for r in priced)
    return dict(races=n,bets=len(started),wins=winners,nonrunner_or_unsettled=n-len(started),
       strike_rate_pct=round(100*winners/len(started),3) if started else None,
       priced_bets=len(priced),missing_sp_bets=len(started)-len(priced),
       profit_1u=round(profit,4),roi_priced_pct=round(100*profit/len(priced),3) if priced else None,
       sp_coverage_pct=round(100*len(priced)/len(started),2) if started else None)


def evaluate_one(races,parameters,weights,save=False):
    selected=[]
    for race in races:
        if not race['verified']:continue
        i=ranks(race,parameters,weights)[0]
        obs=race['settlement'][i]
        selected.append({**{k:race[k] for k in BASIC_FIELDS},'horse':race['horses'][i],
             'model':parameters['model'],'candidate':parameters['id'],'rank':1,
             **{k:obs[k] for k in ('started','won','finish','sp','sp_decimal','sp_source')}})
    return selected if save else stats(selected)


def objective_value(s,baseline,objective):
    if s['bets']<min(10,max(5,int(.72*baseline['bets']))):return -1e9
    rate=s['wins']/max(1,s['bets'])
    baseline_rate=baseline['wins']/max(1,baseline['bets'])
    coverage=s['priced_bets']/max(1,s['bets'])
    missing_penalty=max(0,.70-coverage)*.65
    # A profile without SP cannot earn a fabricated ROI bonus.
    roi=(s['profit_1u']/s['priced_bets']) if s['priced_bets']>=max(8,int(.55*s['bets'])) else -1.
    base_roi=(baseline['profit_1u']/baseline['priced_bets']) if baseline['priced_bets'] else -1.
    delta_roi=max(-2.,min(2.,roi-base_roi))
    if objective=='STRIKE_RATE':return rate-missing_penalty
    if objective=='ROI':return delta_roi-missing_penalty-0.15*max(0.,baseline_rate-rate)
    return .65*(rate-baseline_rate)+.35*.35*delta_roi-missing_penalty


def choose_best(train,model,objective,allopts,weights,baseline):
    old=evaluate_one(train,baseline,weights)
    best=baseline;best_score=objective_value(old,old,objective)
    leaderboard=[]
    for option in allopts:
        s=evaluate_one(train,option,weights)
        score=objective_value(s,old,objective)
        leaderboard.append((score,option,s))
        # Require material improvement before replacing a frozen benchmark,
        # in addition to objective gain (discourages noise-chasing).
        if score>best_score+0.005:
            best,best_score=option,score
    leaderboard.sort(key=lambda x:x[0],reverse=True)
    return best,leaderboard[:10]


def mode_train(races):return [r for r in races if r['date']<=TRAIN_END and r['verified']]
def mode_hold(races):return [r for r in races if r['date']>=HOLD_START and r['verified']]


def settle(args):
    out=pathlib.Path(args.out)
    repro=verify_baseline(out,args.reference)
    outcomes,count,source_audit=attach_results(out,pathlib.Path(args.root))
    training=mode_train(outcomes);hold=mode_hold(outcomes)
    if len(training)<60 or len(hold)<35:
        raise RuntimeError(f'Unsafe chronology: {len(training)} training races, {len(hold)} later races')
    raw_manifest=json.loads((out/'stage98-blind-manifest.json').read_text())
    profmap=profiles(raw_manifest['original_D_weights'])
    pool=candidates();by_model={m:[p for p in pool if p['model']==m] for m in MODELS}
    original={m:next(p for p in pool if p['id']==bid) for m,bid in baseline_ids().items()}
    defined_groups={'OVERALL_NH':lambda x:True,
       'HURDLE':lambda x:x['discipline']=='HURDLE',
       'CHASE':lambda x:x['discipline']=='CHASE',
       'NH_FLAT':lambda x:x['discipline']=='NH_FLAT'}
    for race_type in sorted({r['race_type'] for r in outcomes}):
        defined_groups['TYPE:'+race_type]=lambda x,t=race_type:x['race_type']==t
    def size(group):return len([r for r in training if defined_groups[group](r)])
    trained={};leaders=[];metrics=[];chosen=[];bydate=[];type_profiles=[];fit_cache={}
    # Fit only using earlier dates; no outcome from heldout is ever used to
    # choose a model, pillar weight, blend or fallback.
    for model in MODELS:
        for objective in OBJECTIVES:
            for group,predicate in defined_groups.items():
                fits=[r for r in training if predicate(r)]
                if group.startswith('TYPE:') and len(fits)<MIN_TRAIN_TYPE:
                    fallback='HURDLE' if any(r['discipline']=='HURDLE' for r in fits) else 'CHASE' if any(r['discipline']=='CHASE' for r in fits) else 'NH_FLAT'
                    if size(fallback)<MIN_TRAIN_DISC:fallback='OVERALL_NH'
                elif group in SUBTYPES and len(fits)<MIN_TRAIN_DISC:
                    fallback='OVERALL_NH'
                else:fallback=group
                # Deliberately report the best HISTORICAL fit, but use the
                # conservative fallback where sample is too small.
                candidate_fit=[r for r in training if defined_groups[fallback](r)]
                cache_key=(model,objective,fallback)
                if cache_key not in fit_cache:
                    fit_cache[cache_key]=choose_best(candidate_fit,model,objective,by_model[model],profmap,original[model])
                best,top=fit_cache[cache_key]
                trained[(model,objective,group)]=dict(option=best,fit_group=fallback,train_size=len(candidate_fit))
                if group in ('OVERALL_NH',*SUBTYPES):
                    for rank,(grade,option,observed) in enumerate(top,1):
                        leaders.append(dict(model=model,objective=objective,group=group,
                            candidate=option['id'],candidate_rank=rank,score=round(grade,5),
                            **{f'train_{k}':v for k,v in observed.items()}))
                tt=[r for r in training if predicate(r)]
                hh=[r for r in hold if predicate(r)]
                for split,group_rows in (('TRAIN',tt),('HELDOUT',hh),('FULL_PERIOD',tt+hh)):
                    current=evaluate_one(group_rows,best,profmap)
                    old=evaluate_one(group_rows,original[model],profmap)
                    metrics.append(dict(model=model,objective=objective,group=group,split=split,
                        fitted_from=fallback,selected_candidate=best['id'],
                        **current,baseline_candidate=original[model]['id'],
                        baseline_wins=old['wins'],baseline_strike_rate_pct=old['strike_rate_pct'],
                        baseline_roi_priced_pct=old['roi_priced_pct'],
                        uplift_strike_rate_pp=(round(current['strike_rate_pct']-old['strike_rate_pct'],3)
                            if current['strike_rate_pct'] is not None and old['strike_rate_pct'] is not None else None),
                        uplift_roi_pp=(round(current['roi_priced_pct']-old['roi_priced_pct'],3)
                            if current['roi_priced_pct'] is not None and old['roi_priced_pct'] is not None else None)))
                type_profiles.append(dict(model=model,objective=objective,group=group,
                    eligible_train_races=len(fits),fit_group=fallback,
                    fitting_train_races=len(candidate_fit),selected_candidate=best['id'],
                    d_profile=best['d_profile'],c_share=best['c_share'],
                    d_evidence_share=best['d_evidence_share'],
                    pillar_weights_pct=json.dumps({k:round(100*v,2) for k,v in zip(PILLARS,nh_weights(best['d_profile'],
                      'NH_FLAT' if 'NH Flat' in group else 'CHASE' if 'Chase' in group or group=='CHASE' else 'HURDLE',profmap))}),
                    needs_new_prospective_validation=True))
    # A deployable race-type strategy: use independently optimised profiles,
    # falling back to discipline for small race types. One Rank 1 per race.
    for model in MODELS:
        for objective in OBJECTIVES:
            for split,universe in (('TRAIN',training),('HELDOUT',hold),('FULL_PERIOD',training+hold)):
                selected=[];original_rows=[]
                for race in universe:
                    key=(model,objective,'TYPE:'+race['race_type'])
                    prof=trained[key]
                    picked=evaluate_one([race],prof['option'],profmap,save=True)
                    base=evaluate_one([race],original[model],profmap,save=True)
                    if picked:
                        selected.append(picked[0]);original_rows.extend(base)
                        if split=='FULL_PERIOD':
                            chosen.append(dict(**picked[0],strategy=model+'__'+objective,
                                fitted_from=prof['fit_group'],is_heldout=race['date']>=HOLD_START,
                                d_profile=prof['option']['d_profile'],
                                c_share=prof['option']['c_share'],
                                d_evidence_share=prof['option']['d_evidence_share']))
                now=stats(selected);old=stats(original_rows)
                metrics.append(dict(model=model,objective=objective,group='TYPE_SWITCHED_ALL_NH',
                    split=split,fitted_from='RACE_TYPE_WITH_FALLBACK',
                    selected_candidate='VARIABLE_BY_TYPE',**now,
                    baseline_candidate=original[model]['id'],baseline_wins=old['wins'],
                    baseline_strike_rate_pct=old['strike_rate_pct'],
                    baseline_roi_priced_pct=old['roi_priced_pct'],
                    uplift_strike_rate_pp=(round(now['strike_rate_pct']-old['strike_rate_pct'],3)
                        if now['strike_rate_pct'] is not None and old['strike_rate_pct'] is not None else None),
                    uplift_roi_pp=(round(now['roi_priced_pct']-old['roi_priced_pct'],3)
                        if now['roi_priced_pct'] is not None and old['roi_priced_pct'] is not None else None)))
                for d in sorted({r['date'] for r in selected}):
                    bydate.append(dict(model=model,objective=objective,date=d,
                        **stats([r for r in selected if r['date']==d])))
    # Always show raw baseline against fixed rank1 coverage, including blanks.
    baselines=[]
    for model in MODELS:
        for split,part in (('TRAIN',training),('HELDOUT',hold),('FULL_PERIOD',training+hold)):
            for group,predicate in defined_groups.items():
                rr=[r for r in part if predicate(r)]
                baselines.append(dict(model=model,split=split,group=group,
                        **evaluate_one(rr,original[model],profmap)))
    write_csv(out/'stage98-baseline-scorecard.csv',baselines)
    write_csv(out/'stage98-optimal-profiles-by-race-type.csv',type_profiles)
    write_csv(out/'stage98-combination-scorecard.csv',metrics)
    write_csv(out/'stage98-profile-leaderboards.csv',leaders)
    write_csv(out/'stage98-combined-rank1-selections.csv',chosen)
    write_csv(out/'stage98-daily-combinations.csv',bydate)
    write_csv(out/'stage98-candidates.csv',pool)
    report={'stage':98,'version':VERSION,'status':'RESEARCH_ONLY_NO_LIVE_CHANGES',
      'period':[START,END],'fit_through':TRAIN_END,'heldout_start':HOLD_START,
      'date_coverage':raw_manifest['by_date'],'total_preoff_races':raw_manifest['nh_races'],
      'verified_races':count,'training_verified_races':len(training),'heldout_verified_races':len(hold),
      'candidate_count':len(pool),'models':list(MODELS),
      'Stage97_baseline_reproduced':repro['same_rank1_rank2'],
      'original_baselines':baseline_ids(),
      'source_audit':source_audit,
      'feature_file_sha256':raw_manifest['features_sha256'],
      'main_summary':[r for r in metrics if r['group']=='TYPE_SWITCHED_ALL_NH' and r['split'] in ('HELDOUT','FULL_PERIOD')],
      'methodology':[
        'Full fresh multi-runner D/C and E-no-original-HPR factor reconstruction, NOT switching prior Stage97 selections.',
        'All models use strictly pre-2026-10-01 results data with race-day morning card features only.',
        'No finishing positions, prices or race outcomes are visible during feature reconstruction.',
        'Weights are selected on 18 Oct through 5 Nov; performance from 6 Nov through 10 Nov is evaluated afterward.',
        'Many race-type training samples are too small. Those fall back to discipline or global weights.',
        'Even heldout dates had previously been inspected as part of earlier work; not an untouched prospective test.',
        'Different strike and ROI objectives induce different selected parameters; no promise either increases both.',
        'Historical SP is only reported when uniquely verified, no price is fabricated or inferred from pre-off odds.',
        'Some novel Rank1 horses lack SP and are listed separately. Their missing prices must be recovered before relying on ROI.',
        'All historical source and model recipes are immutable; all results are research only and never deployed live.'
      ]}
    (out/'stage98-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'stage':98,'train':len(training),'holdout':len(hold),
                      'main_summary':report['main_summary']},indent=2),flush=True)


def self_test():
    assert len(candidates())==len(D_PROFILES)*len(C_SHARES)+len(E_PROFILES)*len(D_PART_SHARES)*len(C_SHARES)
    assert TRAIN_END < HOLD_START and CUTOFF<=START<=END
    base=profiles([.15,.20,.15,.10,.05,.15,.10,.10])
    field={'date':'2026-11-03','course':'Cheltenham','time_utc':'14:00','discipline':'HURDLE',
       'race_type':'Handicap Hurdle','horses':['A','B','C'],
       'd_components':[[3,2,1,0,0,1,1,2],[2,3,1,2,0,0,2,1],[1,0,1,1,0,2,0,3]],
       'contemporary_components':[[60,50,40,70,50,60,50,70],[45,65,50,55,50,65,50,55],[50,45,60,60,50,45,50,60]],
       'c_probability':[.45,.30,.25]}
    for c in candidates():
        s=score_race(field,c,base);assert np.isfinite(s).all() and len(s)==3
        assert sorted(ranks(field,c,base))==[0,1,2]
    assert baseline_ids()[MODEL_E] in {x['id'] for x in candidates()}
    assert 1<=len(verified_recovered_prices())<=300
    print('PASS Stage98: expanded 595-profile full-field blends, frozen preoff components, race-type gates, price provenance')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',required=True,choices=('self-test','predict','settle'))
    p.add_argument('--root',type=pathlib.Path,default=pathlib.Path('.'))
    p.add_argument('--db',type=pathlib.Path,default=pathlib.Path('reports/stage98/base/backfilled_research_only.sqlite'))
    p.add_argument('--ratings',type=pathlib.Path,default=pathlib.Path('reports/stage98/empty-hpr.sqlite'))
    p.add_argument('--out',type=pathlib.Path,default=pathlib.Path('reports/stage98/output'))
    p.add_argument('--reference',type=pathlib.Path,default=pathlib.Path('reports/stage98/reference'))
    a=p.parse_args()
    if a.mode=='self-test':self_test()
    elif a.mode=='predict':predict(a)
    else:settle(a)

if __name__=='__main__':main()
