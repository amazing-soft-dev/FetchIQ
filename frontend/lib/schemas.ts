import { z } from "zod";

import type { SearchCriteria } from "./types";

const wholeAge = "Account age must be a whole number of years and months.";
const ageRange = "Years must be 0 to 100 and months must be 0 to 11.";
const ageOrder = "Minimum account age must be less than or equal to the maximum.";

const accountAgeSchema = z
  .object({
    min_years: z.number(),
    min_months: z.number(),
    max_years: z.number(),
    max_months: z.number(),
  })
  .superRefine((age, ctx) => {
    const fields = [age.min_years, age.min_months, age.max_years, age.max_months];
    if (fields.some((value) => !Number.isInteger(value) || value < 0)) {
      ctx.addIssue({ code: "custom", message: wholeAge });
      return;
    }
    if (age.min_months > 11 || age.max_months > 11 || age.min_years > 100 || age.max_years > 100) {
      ctx.addIssue({ code: "custom", message: ageRange });
      return;
    }
    if (age.min_years * 12 + age.min_months > age.max_years * 12 + age.max_months) {
      ctx.addIssue({ code: "custom", message: ageOrder });
    }
  });

export const searchCriteriaSchema = z.object({
  countries: z.array(z.string()).min(1, "Select at least one country."),
  us_states: z.array(z.string()),
  keywords: z.array(z.string()),
  account_age: accountAgeSchema,
  candidate_limit: z.number().superRefine((limit, ctx) => {
    if (!Number.isInteger(limit) || limit < 1 || limit > 5000) {
      ctx.addIssue({
        code: "custom",
        message: "Candidate limit must be between 1 and 5000.",
      });
    }
  }),
});

export function searchCriteriaError(criteria: SearchCriteria): string | null {
  const parsed = searchCriteriaSchema.safeParse(criteria);
  if (parsed.success) {
    return null;
  }
  return parsed.error.issues[0]?.message ?? "Check the search and try again.";
}

export const messageDraftSchema = z
  .object({
    subject: z.string().trim().min(1, "Subject is required.").max(200),
    body: z.string().trim().min(1, "Message is required.").max(20000),
    hourlyLimit: z
      .number()
      .int("Maximum per hour must be a whole number.")
      .min(1, "Maximum per hour must be at least 1.")
      .max(500, "Maximum per hour must be 500 or less."),
    delayMin: z
      .number()
      .int("Delay must be a whole number of seconds.")
      .min(0, "Delay must be 0 or more.")
      .max(3600, "Delay must be 3600 seconds or less."),
    delayMax: z
      .number()
      .int("Delay must be a whole number of seconds.")
      .min(0, "Delay must be 0 or more.")
      .max(3600, "Delay must be 3600 seconds or less."),
  })
  .refine((draft) => draft.delayMin <= draft.delayMax, {
    message: "Delay from must be less than or equal to delay to.",
  });

export function messageDraftError(draft: {
  subject: string;
  body: string;
  hourlyLimit: number;
  delayMin: number;
  delayMax: number;
}): string | null {
  const parsed = messageDraftSchema.safeParse(draft);
  if (parsed.success) {
    return null;
  }
  return parsed.error.issues[0]?.message ?? "Check the message and try again.";
}
