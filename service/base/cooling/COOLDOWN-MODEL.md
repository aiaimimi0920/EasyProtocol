# EasyProtocol Cooldown Model

This file records the service-cooling skeleton for `EasyProtocol`.

## Cooling Goal

Cooling should temporarily suppress language-specific services that repeatedly
fail.

## Basic Rule

After a service reaches the configured failure threshold, it should enter a
cooldown window for the configured duration.

## Cooling Scope

Cooling currently applies at the language-service level.

Possible cooled services:

- `GolangProtocol`
- `JSProtocol`
- `PythonProtocol`
- `RustProtocol`

## Cooling Inputs

Cooling may eventually use:

- repeated delegation failures
- repeated runtime failures
- repeated timeout failures
- repeated transport failures
