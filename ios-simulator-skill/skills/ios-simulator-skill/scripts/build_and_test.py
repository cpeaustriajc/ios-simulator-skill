#!/usr/bin/env python3
"""
Build and Test Automation for Xcode Projects

Ultra token-efficient build automation with progressive disclosure via xcresult bundles.

Features:
- Minimal default output (5-10 tokens)
- Progressive disclosure for error/warning/log details
- Native xcresult bundle support
- Clean modular architecture

Usage Examples:
    # Build (minimal output)
    python scripts/build_and_test.py --project MyApp.xcodeproj
    # Output: Build: SUCCESS (0 errors, 3 warnings) [xcresult-20251018-143052]

    # Get error details
    python scripts/build_and_test.py --get-errors xcresult-20251018-143052

    # Get warnings
    python scripts/build_and_test.py --get-warnings xcresult-20251018-143052

    # Get build log
    python scripts/build_and_test.py --get-log xcresult-20251018-143052

    # Get everything as JSON
    python scripts/build_and_test.py --get-all xcresult-20251018-143052 --json

    # List recent builds
    python scripts/build_and_test.py --list-xcresults

    # Verbose mode (for debugging)
    python scripts/build_and_test.py --project MyApp.xcodeproj --verbose
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Import our modular components
from xcode import BuildRunner, OutputFormatter, XCResultCache, XCResultParser


def _resolve_target_udid(simulator_name: str | None) -> str:
    """Pick the simctl target — "booted" by default, or `--simulator <name>`."""
    return simulator_name or "booted"


def _install_and_launch(
    *,
    udid_or_booted: str,
    app_path: str,
    bundle_id: str | None,
    launch: bool,
) -> tuple[str | None, str | None]:
    """
    Install (and optionally launch) the built .app via simctl.

    Returns (install_status, launch_status). Status strings are agent-friendly
    one-liners — success or a short error message. Never raises.
    """
    install_status: str | None = None
    launch_status: str | None = None
    try:
        result = subprocess.run(
            ["xcrun", "simctl", "install", udid_or_booted, app_path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            install_status = f"Installed: {app_path}"
        else:
            install_status = f"Install failed: {result.stderr.strip() or 'simctl install non-zero'}"
            return (install_status, None)
    except FileNotFoundError:
        return ("Install failed: xcrun not on PATH", None)

    if launch and bundle_id:
        try:
            result = subprocess.run(
                ["xcrun", "simctl", "launch", udid_or_booted, bundle_id],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                launch_status = f"Launched: {bundle_id}"
            else:
                launch_status = (
                    f"Launch failed: {result.stderr.strip() or 'simctl launch non-zero'}"
                )
        except FileNotFoundError:
            launch_status = "Launch failed: xcrun not on PATH"
    elif launch and not bundle_id:
        launch_status = "Launch skipped: bundle id not resolved from build settings"

    return (install_status, launch_status)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build and test Xcode projects with progressive disclosure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build project (minimal output)
  python scripts/build_and_test.py --project MyApp.xcodeproj

  # Run tests
  python scripts/build_and_test.py --project MyApp.xcodeproj --test

  # Get error details from previous build
  python scripts/build_and_test.py --get-errors xcresult-20251018-143052

  # Get all details as JSON
  python scripts/build_and_test.py --get-all xcresult-20251018-143052 --json

  # List recent builds
  python scripts/build_and_test.py --list-xcresults
        """,
    )

    # Build/test mode arguments
    build_group = parser.add_argument_group("Build/Test Options")
    project_group = build_group.add_mutually_exclusive_group()
    project_group.add_argument("--project", help="Path to .xcodeproj file")
    project_group.add_argument("--workspace", help="Path to .xcworkspace file")

    build_group.add_argument("--scheme", help="Build scheme (auto-detected if not specified)")
    build_group.add_argument(
        "--configuration",
        default="Debug",
        help="Build configuration (default: Debug). Accepts any valid Xcode configuration.",
    )
    build_group.add_argument("--simulator", help="Simulator name (default: iPhone 15)")
    build_group.add_argument("--clean", action="store_true", help="Clean before building")
    build_group.add_argument("--test", action="store_true", help="Run tests")
    build_group.add_argument(
        "--build-only",
        action="store_true",
        help=(
            "Explicit alias for build-only mode. "
            "Equivalent to omitting --test (which is the default)."
        ),
    )
    build_group.add_argument("--suite", help="Specific test suite to run")
    build_group.add_argument(
        "--install",
        action="store_true",
        help=(
            "After a successful build, install the .app onto the booted "
            "(or --simulator) simulator."
        ),
    )
    build_group.add_argument(
        "--install-and-launch",
        action="store_true",
        help="--install plus `simctl launch` of the resolved bundle ID.",
    )

    # Progressive disclosure arguments
    disclosure_group = parser.add_argument_group("Progressive Disclosure Options")
    disclosure_group.add_argument(
        "--get-errors", metavar="XCRESULT_ID", help="Get error details from xcresult"
    )
    disclosure_group.add_argument(
        "--get-warnings", metavar="XCRESULT_ID", help="Get warning details from xcresult"
    )
    disclosure_group.add_argument(
        "--get-log", metavar="XCRESULT_ID", help="Get build log from xcresult"
    )
    disclosure_group.add_argument(
        "--get-all", metavar="XCRESULT_ID", help="Get all details from xcresult"
    )
    disclosure_group.add_argument(
        "--list-xcresults", action="store_true", help="List recent xcresult bundles"
    )

    # Output options
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument("--verbose", action="store_true", help="Show detailed output")
    output_group.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Initialize cache
    cache = XCResultCache()

    # Handle list mode
    if args.list_xcresults:
        xcresults = cache.list()
        if args.json:
            import json

            print(json.dumps(xcresults, indent=2))
        elif not xcresults:
            print("No xcresult bundles found")
        else:
            print(f"Recent XCResult bundles ({len(xcresults)}):")
            print()
            for xc in xcresults:
                print(f"  {xc['id']}")
                print(f"    Created: {xc['created']}")
                print(f"    Size: {xc['size_mb']} MB")
                print()
        return 0

    # Handle retrieval modes
    xcresult_id = args.get_errors or args.get_warnings or args.get_log or args.get_all

    if xcresult_id:
        xcresult_path = cache.get_path(xcresult_id)

        if not xcresult_path or not xcresult_path.exists():
            print(f"Error: XCResult bundle not found: {xcresult_id}", file=sys.stderr)
            print("Use --list-xcresults to see available bundles", file=sys.stderr)
            return 1

        # Load cached stderr for progressive disclosure
        cached_stderr = cache.get_stderr(xcresult_id)
        parser = XCResultParser(xcresult_path, stderr=cached_stderr)

        # Get errors
        if args.get_errors:
            errors = parser.get_errors()
            if args.json:
                import json

                print(json.dumps(errors, indent=2))
            else:
                print(OutputFormatter.format_errors(errors))
            return 0

        # Get warnings
        if args.get_warnings:
            warnings = parser.get_warnings()
            if args.json:
                import json

                print(json.dumps(warnings, indent=2))
            else:
                print(OutputFormatter.format_warnings(warnings))
            return 0

        # Get log
        if args.get_log:
            log = parser.get_build_log()
            if log:
                print(OutputFormatter.format_log(log))
            else:
                print("No build log available", file=sys.stderr)
                return 1
            return 0

        # Get all
        if args.get_all:
            error_count, warning_count = parser.count_issues()
            errors = parser.get_errors()
            warnings = parser.get_warnings()
            build_log = parser.get_build_log()

            if args.json:
                import json

                data = {
                    "xcresult_id": xcresult_id,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "errors": errors,
                    "warnings": warnings,
                    "log_preview": build_log[:1000] if build_log else None,
                }
                print(json.dumps(data, indent=2))
            else:
                print(f"XCResult: {xcresult_id}")
                print(f"Errors: {error_count}, Warnings: {warning_count}")
                print()
                if errors:
                    print(OutputFormatter.format_errors(errors, limit=10))
                    print()
                if warnings:
                    print(OutputFormatter.format_warnings(warnings, limit=10))
                    print()
                if build_log:
                    print("Build Log (last 30 lines):")
                    print(OutputFormatter.format_log(build_log, lines=30))
            return 0

    # Build/test mode
    if not args.project and not args.workspace:
        # Try to auto-detect in current directory
        cwd = Path.cwd()
        projects = list(cwd.glob("*.xcodeproj"))
        workspaces = list(cwd.glob("*.xcworkspace"))

        if workspaces:
            args.workspace = str(workspaces[0])
        elif projects:
            args.project = str(projects[0])
        else:
            parser.error("No project or workspace specified and none found in current directory")

    # Initialize builder
    builder = BuildRunner(
        project_path=args.project,
        workspace_path=args.workspace,
        scheme=args.scheme,
        configuration=args.configuration,
        simulator=args.simulator,
        cache=cache,
    )

    # Validate flag combos
    if args.test and (args.install or args.install_and_launch):
        print(
            "Error: --install / --install-and-launch are only valid with build mode",
            file=sys.stderr,
        )
        return 1
    if args.test and args.build_only:
        print("Error: --build-only and --test are mutually exclusive", file=sys.stderr)
        return 1

    # Execute build or test
    if args.test:
        success, xcresult_id, stderr = builder.test(test_suite=args.suite)
    else:
        success, xcresult_id, stderr = builder.build(clean=args.clean)

    if not xcresult_id and not stderr:
        print("Error: Build/test failed without creating xcresult or error output", file=sys.stderr)
        return 1

    # Save stderr to cache for progressive disclosure
    if xcresult_id and stderr:
        cache.save_stderr(xcresult_id, stderr)

    # Parse results
    xcresult_path = cache.get_path(xcresult_id) if xcresult_id else None
    parser = XCResultParser(xcresult_path, stderr=stderr)
    error_count, warning_count = parser.count_issues()

    # Resolve build artifacts (.app path, bundle id, DerivedData) on success.
    # We do this in build mode only — test mode runs xctest, not a packaged app.
    build_artifacts: dict[str, str] = {}
    install_status: str | None = None
    launch_status: str | None = None
    if success and not args.test:
        settings = builder.get_build_settings()
        built_products_dir = settings.get("BUILT_PRODUCTS_DIR", "")
        product_name = settings.get("FULL_PRODUCT_NAME", "")
        bundle_id = settings.get("PRODUCT_BUNDLE_IDENTIFIER", "")
        # OBJROOT (e.g. .../DerivedData/<Hash>/Build/Intermediates.noindex) is the
        # closest thing xcodebuild exposes to "which DerivedData hash am I using".
        # Trim to the DerivedData root for a more useful surface.
        obj_root = settings.get("OBJROOT", "")
        derived_data_root = ""
        if obj_root and "/DerivedData/" in obj_root:
            head, _, tail = obj_root.partition("/DerivedData/")
            hash_dir = tail.split("/", 1)[0]
            derived_data_root = f"{head}/DerivedData/{hash_dir}"

        if built_products_dir and product_name:
            from pathlib import Path as _Path

            built_app_path = str(_Path(built_products_dir) / product_name)
            build_artifacts["built_app_path"] = built_app_path
        if bundle_id:
            build_artifacts["bundle_identifier"] = bundle_id
        if derived_data_root:
            build_artifacts["derived_data_path"] = derived_data_root

        # Handle install / install-and-launch.
        do_install = args.install or args.install_and_launch
        do_launch = args.install_and_launch
        if do_install and build_artifacts.get("built_app_path"):
            install_status, launch_status = _install_and_launch(
                udid_or_booted=_resolve_target_udid(args.simulator),
                app_path=build_artifacts["built_app_path"],
                bundle_id=build_artifacts.get("bundle_identifier"),
                launch=do_launch,
            )

    # Format output
    status = "SUCCESS" if success else "FAILED"

    # Collect errors on failure (used by all output modes)
    errors = parser.get_errors() if not success else None
    hints = OutputFormatter.generate_hints(errors) if errors else None

    # Collect test info and failed tests when testing
    test_info = None
    failed_tests = None
    if args.test and xcresult_path:
        test_results = parser.get_test_results()
        if test_results:
            test_info = {
                "total": test_results.get("total", 0),
                "passed": test_results.get("passed", 0),
                "failed": test_results.get("failed", 0),
                "duration": test_results.get("duration", 0.0),
            }
        if not success:
            failed_tests = parser.get_failed_tests()

    if args.verbose:
        # Verbose mode with error/warning details
        verbose_errors = errors if error_count > 0 else None
        warnings = parser.get_warnings() if warning_count > 0 else None

        output = OutputFormatter.format_verbose(
            status=status,
            error_count=error_count,
            warning_count=warning_count,
            xcresult_id=xcresult_id or "N/A",
            errors=verbose_errors,
            warnings=warnings,
            test_info=test_info,
        )
        print(output)
        # Always surface the DerivedData path in verbose mode (fix #5).
        if build_artifacts:
            print()
            if build_artifacts.get("built_app_path"):
                print(f"App: {build_artifacts['built_app_path']}")
            if build_artifacts.get("bundle_identifier"):
                print(f"Bundle ID: {build_artifacts['bundle_identifier']}")
            if build_artifacts.get("derived_data_path"):
                print(f"DerivedData: {build_artifacts['derived_data_path']}")
        if install_status:
            print(install_status)
        if launch_status:
            print(launch_status)
    elif args.json:
        # JSON mode
        data = {
            "success": success,
            "xcresult_id": xcresult_id or None,
            "error_count": error_count,
            "warning_count": warning_count,
        }
        if test_info:
            data["test_info"] = test_info
        if build_artifacts:
            data.update(build_artifacts)
        if install_status:
            data["install_status"] = install_status
        if launch_status:
            data["launch_status"] = launch_status
        if not success:
            if errors:
                data["errors"] = errors[:10]
            if failed_tests:
                data["failed_tests"] = failed_tests[:10]
        if hints:
            data["hints"] = hints
        import json

        print(json.dumps(data, indent=2))
    else:
        # Minimal mode (default)
        output = OutputFormatter.format_minimal(
            status=status,
            error_count=error_count,
            warning_count=warning_count,
            xcresult_id=xcresult_id or "N/A",
            test_info=test_info,
            hints=hints,
            errors=errors,
            failed_tests=failed_tests,
        )
        print(output)
        # Surface artifacts inline for the build → install → launch loop (fix #3).
        # One terse line each, after the build status line.
        if build_artifacts.get("built_app_path"):
            print(f"App: {build_artifacts['built_app_path']}")
        if build_artifacts.get("bundle_identifier"):
            print(f"Bundle ID: {build_artifacts['bundle_identifier']}")
        if install_status:
            print(install_status)
        if launch_status:
            print(launch_status)

    # Exit with appropriate code
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
