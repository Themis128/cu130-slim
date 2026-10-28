'use client'

import { useState, type ReactNode } from 'react'
import { ArrowLeft, ArrowRight, Check } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { cn } from '@/lib/utils'

export interface WizardStep {
  title: string
  description?: string
  /** Render the step body. Returns whether the step is valid to proceed. */
  render: (ctx: WizardStepContext) => ReactNode
}

export interface WizardStepContext {
  /** Move to the next step (call from a button inside the step body) */
  next: () => void
  /** Move to the previous step */
  back: () => void
  /** The 0-based index of the current step */
  currentStep: number
  /** Total number of steps */
  totalSteps: number
}

interface WizardProps {
  steps: WizardStep[]
  onComplete: () => void
  /** Optional className for the outer container */
  className?: string
}

/**
 * Multi-step wizard container with a progress indicator and next/back navigation.
 *
 * Each step's `render` function receives a context with `next`/`back` helpers
 * so step bodies can embed their own navigation buttons (e.g. "Continue",
 * "Skip for now") while the wizard manages the progress bar and state.
 */
export function Wizard({ steps, onComplete, className }: WizardProps) {
  const [currentStep, setCurrentStep] = useState(0)
  const totalSteps = steps.length
  const step = steps[currentStep]
  const isLast = currentStep === totalSteps - 1

  const next = () => {
    if (isLast) {
      onComplete()
    } else {
      setCurrentStep(s => Math.min(s + 1, totalSteps - 1))
    }
  }

  const back = () => {
    setCurrentStep(s => Math.max(s - 1, 0))
  }

  const progress = Math.round(((currentStep + 1) / totalSteps) * 100)

  return (
    <div className={cn('mx-auto max-w-2xl space-y-6', className)}>
      {/* Progress indicator */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">
            Step {currentStep + 1} of {totalSteps}
          </span>
          <span className="text-muted-foreground">{step.title}</span>
        </div>
        <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
        {/* Step dots */}
        <div className="flex items-center justify-center gap-2 pt-2">
          {steps.map((s, i) => (
            <div
              key={i}
              className={cn(
                'flex items-center gap-1.5',
                i < currentStep && 'text-primary',
                i === currentStep && 'text-primary font-medium',
                i > currentStep && 'text-muted-foreground'
              )}
            >
              {i < currentStep ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <span
                  className={cn(
                    'flex h-5 w-5 items-center justify-center rounded-full border text-xs',
                    i === currentStep
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-muted-foreground/30'
                  )}
                >
                  {i + 1}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Step body */}
      <div className="min-h-[300px]">
        {step.render({ next, back, currentStep, totalSteps })}
      </div>

      {/* Default navigation (hidden on last step — the step body handles it) */}
      {!isLast && (
        <div className="flex items-center justify-between">
          <Button
            variant="ghost"
            onClick={back}
            disabled={currentStep === 0}
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
          <Button onClick={next}>
            Continue
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  )
}
