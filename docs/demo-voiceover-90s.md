# 90-Second Demo Voiceover

## Voice Script

[0:00]  
This demo shows not just the diagnosis, but the decision process behind it.

[0:06]  
We start with a real incident case: `h009_liveness_probe_loop`.

[0:11]  
The service is restarting because the liveness probe timing has drifted.

[0:16]  
First, we run the case with a strict threshold profile.

[0:20]  
The system explores a plausible path, scores it, and follows the evidence.

[0:26]  
But the confidence does not improve enough.

[0:29]  
So the branch is marked as a dead end.

[0:33]  
That matters because the failure is visible.

[0:36]  
We can see where the path stalled, why it was rejected, and backtrack to an earlier decision point.

[0:45]  
Now we run the exact same case again, but with a different threshold profile.

[0:51]  
Same problem, same signals, different routing policy.

[0:56]  
This time, the system converges more cleanly toward the useful path.

[1:01]  
So the goal is not only to produce an answer.

[1:05]  
The goal is to make the reasoning explicit, reviewable, and tunable.

[1:11]  
Next, we switch to `h006_networkpolicy_blocked`.

[1:15]  
Here, the system reaches a valid remediation path for a networking issue.

[1:21]  
But it does not act automatically.

[1:24]  
Instead, it stops at the human decision gate.

[1:28]  
The operator can review the recommendation and choose to approve or reject it.

[1:35]  
That is the key point of the demo.

[1:38]  
The system can explore, compare, and justify decisions.

[1:43]  
But the final operational choice remains under human control.

## Click Track

- Load `h009_liveness_probe_loop`
- Select `Manual (step-by-step)`
- Select `Strict demo`
- Click `Run simulation`
- Follow one branch to a `dead end`
- Click `Backtrack`
- Click `Compare strict vs lenient`
- Rerun with `Lenient demo`
- Switch to `h006_networkpolicy_blocked`
- Run until `Resolution found`
- Show `Operator decision`
- Click `approve` or `reject`

## TTS-Friendly Script

This demo shows more than a diagnosis.

It shows the decision process.

We start with a real incident case.

`h009_liveness_probe_loop`.

The service keeps restarting.

The liveness probe timing has drifted.

First, we run the case with a strict threshold profile.

The system explores a plausible path.

It scores that path.

It follows the available evidence.

But the confidence does not improve enough.

So this branch becomes a dead end.

That is important.

The failure is visible.

We can see where the path stalled.

We can see why it was rejected.

And we can backtrack.

Now we run the exact same case again.

Same problem.

Same signals.

But a different threshold profile.

This time, the system converges more cleanly.

So the goal is not only to produce an answer.

The goal is to make the reasoning explicit.

Reviewable.

And tunable.

Next, we switch to `h006_networkpolicy_blocked`.

Here, the system reaches a valid remediation path.

But it does not act automatically.

It stops at the human decision gate.

The operator reviews the recommendation.

Then chooses to approve or reject it.

That is the key point.

The system can explore.

Compare.

And justify decisions.

But the final operational choice remains under human control.
