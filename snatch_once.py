#!/usr/bin/env python3
"""
OCI Always Free snatch — GitHub Actions edition (Singapore ap-singapore-1).

One process handles BOTH targets:
  - sg-arm-1   VM.Standard.A1.Flex  1 OCPU / 6 GB  (Ubuntu 24.04 aarch64)
  - sg-micro-1 VM.Standard.E2.1.Micro              (Ubuntu 24.04 x86_64)

Designed for a scheduled GitHub Actions job, which is a short-lived
runner, not a daemon:
  - Every round starts with the duplicate guard: list instances first,
    and NEVER launch while the existence of a target is unknown
    (a launch POST that times out may still have created it server-side).
  - RUN_MINUTES=0  -> exactly one round, then exit (private-repo mode,
    pairs with a */30 cron so the monthly free minutes last).
  - RUN_MINUTES=50 -> keep looping rounds every RETRY_SECONDS (300)
    until ~50 minutes elapsed, then exit (public-repo mode, pairs
    with an hourly cron for near-continuous coverage).

Prints a line starting with "SUCCESS" for each instance it creates,
and "ALREADY_EXISTS" for targets that are already there. Exits 2 on a
non-retryable error (LimitExceeded / NotAuthorized / InvalidParameter)
so the Actions run shows red and gets noticed.

Required env (set from GitHub Secrets in the workflow):
  OCI_COMPARTMENT_ID, OCI_SUBNET_ID, OCI_IMAGE_ARM, OCI_IMAGE_X86,
  OCI_SSH_PUBKEY_FILE (path to a file containing the SSH public key)
OCI config is read from ~/.oci/config as usual.
Optional env: OCI_ADS, RETRY_SECONDS, RUN_MINUTES.
"""

import datetime
import os
import sys
import time

try:
    import oci
except ImportError:
    sys.exit("Missing dependency: run `pip install oci` first.")

RETRYABLE_MARKERS = (
    "out of host capacity",
    "internalerror",
    "toomanyrequests",
    "serviceunavailable",
    "temporarily unavailable",
)

ARM_NAME = "sg-arm-1"
MICRO_NAME = "sg-micro-1"


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def main():
    compartment_id = env("OCI_COMPARTMENT_ID", required=True)
    subnet_id = env("OCI_SUBNET_ID", required=True)
    image_arm = env("OCI_IMAGE_ARM", required=True)
    image_x86 = env("OCI_IMAGE_X86", required=True)
    ssh_pubkey_path = os.path.expanduser(env("OCI_SSH_PUBKEY_FILE", "oracle_sg.pub"))
    retry_seconds = int(env("RETRY_SECONDS", "300"))
    run_minutes = int(env("RUN_MINUTES", "0"))
    deadline = time.time() + run_minutes * 60 if run_minutes > 0 else None

    with open(ssh_pubkey_path, "r", encoding="utf-8") as f:
        ssh_pubkey = f.read().strip()

    config = oci.config.from_file()  # ~/.oci/config, profile DEFAULT
    identity = oci.identity.IdentityClient(config)
    compute = oci.core.ComputeClient(config)
    network = oci.core.VirtualNetworkClient(config)

    if os.environ.get("OCI_ADS"):
        ads = [a.strip() for a in os.environ["OCI_ADS"].split(",") if a.strip()]
    else:
        ads = [ad.name for ad in identity.list_availability_domains(compartment_id).data]
    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] ADs: {ads}", flush=True)

    def existing_by_name():
        """Map display_name -> instance for all live instances. Raises on API errors."""
        found = {}
        for inst in compute.list_instances(compartment_id).data:
            if inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
                found[inst.display_name] = inst
        return found

    def public_ip(instance_id):
        try:
            for att in compute.list_vnic_attachments(compartment_id, instance_id=instance_id).data:
                vnic = network.get_vnic(att.vnic_id).data
                if vnic.is_primary:
                    return vnic.public_ip
        except Exception as e:
            print(f"  -> could not read VNIC ({e})", flush=True)
        return None

    def try_launch(name, shape, image_id, shape_config=None):
        """One launch attempt per AD. Returns 'created' | 'retry' | 'fatal'."""
        for ad in ads:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            print(f"[{now}] attempt {name} in {ad}: {shape}", flush=True)
            kwargs = dict(
                availability_domain=ad,
                compartment_id=compartment_id,
                display_name=name,
                shape=shape,
                source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image_id),
                create_vnic_details=oci.core.models.CreateVnicDetails(
                    subnet_id=subnet_id, assign_public_ip=True
                ),
                metadata={"ssh_authorized_keys": ssh_pubkey},
                is_pv_encryption_in_transit_enabled=True,
            )
            if shape_config is not None:
                kwargs["shape_config"] = shape_config
            details = oci.core.models.LaunchInstanceDetails(**kwargs)
            try:
                inst = compute.launch_instance(details).data
                ip = public_ip(inst.id)
                print(f"SUCCESS {name}: instance {inst.id} state={inst.lifecycle_state} public_ip={ip}", flush=True)
                return "created"
            except oci.exceptions.ServiceError as e:
                msg = f"{e.code} {e.message}".lower()
                print(f"  -> {e.code}: {e.message}", flush=True)
                if any(marker in msg for marker in RETRYABLE_MARKERS):
                    continue
                print(f"Non-retryable error for {name} - stop and fix the config/account first.", flush=True)
                return "fatal"
            except oci.exceptions.RequestException as e:
                # Transient (e.g. ReadTimeout on launch): the instance may
                # have been created server-side; the next round's guard
                # check will see it. Do not crash.
                print(f"  -> RequestException (transient, guard will re-check): {e}", flush=True)
                continue
        return "retry"

    arm_config = oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=1.0, memory_in_gbs=6.0)
    done = set()
    round_no = 0
    while True:
        round_no += 1
        # Duplicate guard at the start of every round.
        try:
            existing = existing_by_name()
        except Exception as e:
            print(f"Existence check failed ({e}); not launching this round.", flush=True)
            if deadline is None or time.time() + retry_seconds >= deadline:
                print("Single-round mode or deadline reached; exiting.", flush=True)
                return 0
            time.sleep(retry_seconds)
            continue

        for name in (ARM_NAME, MICRO_NAME):
            if name in done:
                continue
            if name in existing:
                inst = existing[name]
                print(f"ALREADY_EXISTS {name}: instance {inst.id} state={inst.lifecycle_state}", flush=True)
                done.add(name)

        if len(done) == 2:
            print("Both targets exist. Nothing to do.", flush=True)
            return 0

        if ARM_NAME not in done:
            result = try_launch(ARM_NAME, "VM.Standard.A1.Flex", image_arm, arm_config)
            if result == "created":
                done.add(ARM_NAME)
            elif result == "fatal":
                return 2
        if MICRO_NAME not in done:
            result = try_launch(MICRO_NAME, "VM.Standard.E2.1.Micro", image_x86)
            if result == "created":
                done.add(MICRO_NAME)
            elif result == "fatal":
                return 2

        if len(done) == 2:
            print("Both targets created/existing. Done.", flush=True)
            return 0
        if deadline is None or time.time() + retry_seconds >= deadline:
            print(f"Round {round_no} finished, no capacity. Exiting (next scheduled run will retry).", flush=True)
            return 0
        print(f"No capacity this round. Sleeping {retry_seconds}s...", flush=True)
        time.sleep(retry_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
