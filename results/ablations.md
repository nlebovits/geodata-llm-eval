Ablation arms - opus

arm            spec  runs   mean       range         S1         S2         S3         S4         S5         S6
--------------------------------------------------------------------------------------------------------------
full           -        3    81%      70-100    100/100    100/100     86/ 86     85/ 85     56/100     33/100
full           d944     2   100%     100-100    100/100    100/100    100/100    100/100    100/100    100/100
no-coops       3c7a     2    90%      90-90     100/100    100/100    100/100    100/100     67/ 67      0/  -

Per-question pass rate - opus

Q     st     full d944               full -     d        no-coops 3c7a     d
----------------------------------------------------------------------------
q10    3          100%                  67%   -33                 100%     0
q13    3          100%                  33%   -67                 100%     0
q20    4          100%                  33%   -67                 100%     0
q23    4          100%                  33%   -67                 100%     0
q24    5          100%                  33%   -67                 100%     0
q26    5          100%                  33%   -67                 100%     0
q27    5          100%                  33%   -67                 100%     0
q28    5          100%                  33%   -67                   0%  -100
q29    5          100%                 100%     0                   0%  -100
q30    6          100%                  33%   -67                   0%  -100
(20 questions scored the same in every arm, hidden)

! arm full saw 2 different specs (-, d944); they are reported separately, never pooled
! excluded opus/20260726T204402Z-e606096: regraded
! excluded opus/20260731T100603Z-8b7019a: regraded

full: The complete spec. Every other arm reads as a delta against this one, so a sweep must include it rather than reusing an older run.

no-coops: Withholds buyer and cooperative matching entirely. Stages 5 and 6 consume it and stages 1 to 4 do not, so movement in the early stages is cascade or noise rather than an effect of the missing document.

