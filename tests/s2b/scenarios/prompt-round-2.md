Thank you. A review of these 12 scenarios found problems that make most of them unusable as they are. Please

rewrite all 12 following the rules below, which add to the brief, then continue with the remaining families (hpa,

webhook, configmap, pdb, ingress, storage, pullsecret) in the same format. Check every claim about Kubernetes

behaviour against the documentation you cite.


## A. Rules for every scenario


    **Partial credit never contains the answer.** `partial_if_only` lists what is on the path but stops short: the 


   first hop, the symptom, or the right object with a wrong mechanism. It never contains `cause_object`, nor

   anything `passes_if` accepts.

2. **Scenarios with no incident:**

   - The question asks about the present ("is something wrong with X in Y", "why is X slow in Y"), and at T0 nothing

     is wrong in the cluster.

   - `mechanism` describes why each thing that looks wrong is not a fault. It names no cause, inside or outside the

     cluster.

   - `passes_if` is "states that there is no incident and names no fault". It accepts nothing else.

   - The question may not be about intended behaviour ("why is X allowed").

3. **Every hop is a relation Kubernetes defines, carried by fields of both objects.** A NetworkPolicy selects pods

   (`podSelector`, `namespaceSelector`), not Services. "Object → its own Namespace" is not a hop. A scenario has at

   least two such hops between the symptom and the cause object.

4. **Every abnormal state is visible in the sources listed in the brief.** A fact outside Kubernetes, such as a

   database schema change, is visible only through a log line, an alert or a trace that you quote, with its time.

5. **Events come from the component that emits them, worded as it words them.** For example, pod creation failures

   (`FailedCreate`) are events on the ReplicaSet or Job, not on the Deployment. Quote the message as the API

   server or controller writes it.

6. **Quotas and limits count what the documentation says they count.** Compute quotas from `requests` and

   `limits`, not from CPU or memory usage. Check which pods a `pods` quota counts. LimitRange is enforced by the

   built-in LimitRanger admission plugin, not by a webhook.

7. **One cause, one origin, no ambiguity.** If two changes could each explain the symptom, the timeline must show

   which one did. For example, if the workload was edited after the constraint, say whether the old version would

   also fail.

8. **Decoys are visible at T0** in the objects, events, alerts or logs. "Removed yesterday" is not visible. Give

   each decoy the field or event that shows it.

9. **Size.** `objects` lists at least 25 other objects, across at least 2 namespaces, with their kind, namespace,

   name and a one-line state. Most are healthy and unrelated.

10. **Timeline consistency.** Every time in `chain`, `decoys` and `mechanism` appears in `timeline`, and they

    agree.

11. **No textbook chains.** As the brief says, prefer the second or third way a family fails in production. Two

    scenarios of a family must differ in the cause object's kind or in the path, not only in names.


## B. Notes per scenario


    **quota-1:** state the quota in `requests.cpu`. Give the origin: what made the Job request more than usual, and 


  when. The HPA decoy must appear in the timeline.

    **quota-2:** the mechanism contradicts the ResourceQuota documentation on which pods count. Replace it with a 


  different, correct chain.

    **quota-0:** remove the external cause; follow rule A2. 


    **netpol-1:** the hop from the Service to the NetworkPolicy is not a relation; rework the chain. This chain is 


  also a textbook one (rule A11).

    **netpol-2:** fix partial credit (A1). Add the hop that makes the cause at least two relations from the symptom, 


  with its fields.

    **netpol-0:** ask about an incident in the present (A2). 


    **dependency-1:** give the origin object (the release or the change that set the readiness probe) and its fields. 


    **dependency-2:** make the schema change visible (A4), and say which object carries each hop. 


    **dependency-0:** ask about the present; the past incident is a decoy (A2). 


    **limitrange-1:** rule A6 on admission; quote the real message; rule A7 on what changed when; this chain is a 


  textbook one (A11).

    **limitrange-2:** a textbook chain (A11). Replace it with a different path in the same family. 


    **limitrange-0:** make the dates agree (A10); ask about the present. 



Output one YAML document per scenario, in the brief's format. Write nothing about how a tool should find the cause.

If you stop before the end, say where.
