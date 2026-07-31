Ablation arms - opus

arm            spec  runs   mean       range         S1         S2         S3         S4         S5         S6
--------------------------------------------------------------------------------------------------------------
full           d944     2   100%     100-100    100/100    100/100    100/100    100/100    100/100    100/100
no-coops       3c7a     2    90%      90-90     100/100    100/100    100/100    100/100     67/ 67      0/  -

Per-question pass rate - opus

Q     st     full d944        no-coops 3c7a     d
-------------------------------------------------
q28    5          100%                   0%  -100
q29    5          100%                   0%  -100
q30    6          100%                   0%  -100
(27 questions scored the same in every arm, hidden)

full: The complete spec. Every other arm reads as a delta against this one, so a sweep must include it rather than reusing an older run.

no-coops: Withholds buyer and cooperative matching entirely. Stages 5 and 6 consume it and stages 1 to 4 do not, so movement in the early stages is cascade or noise rather than an effect of the missing document.

