"use client"

import * as React from "react"
import { isValidElement } from "react"
import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"

import { cn } from "@/lib/utils"

function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-4", className)}
      {...props}
    />
  )
}

function TabsList({ className, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        "inline-flex h-8 w-fit items-center gap-1 rounded-lg bg-muted p-1",
        className
      )}
      {...props}
    />
  )
}

function TabsTrigger({ className, nativeButton, ...props }: TabsPrimitive.Tab.Props) {
  // Base UI assumes a native <button> unless told otherwise; a `render` that
  // swaps in another element (e.g. a Link) needs nativeButton={false}.
  const rendersNativeButton =
    nativeButton ??
    (props.render === undefined ||
      (isValidElement(props.render) && props.render.type === "button"))
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-trigger"
      nativeButton={rendersNativeButton}
      className={cn(
        "inline-flex h-6 items-center justify-center gap-1.5 rounded-md px-2.5 text-sm font-medium whitespace-nowrap text-muted-foreground outline-none transition-colors select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 data-active:bg-background data-active:text-foreground data-active:shadow-sm",
        className
      )}
      {...props}
    />
  )
}

function TabsContent({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-content"
      className={cn("outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTrigger, TabsContent }
