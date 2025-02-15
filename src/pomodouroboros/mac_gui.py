# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from time import time as rawSeconds
from typing import Callable, ClassVar, Iterable, List, Optional, Tuple

from Foundation import NSRect
from twisted.internet.interfaces import IReactorTCP
from twisted.internet.task import LoopingCall
from twisted.python.failure import Failure

import math
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApp,
    NSApplicationDidChangeScreenParametersNotification,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBorderlessWindowMask,
    NSColor,
    NSEvent,
    NSFloatingWindowLevel,
    NSNotificationCenter,
    NSScreen,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorStationary,
)
from dateutil.tz import tzlocal
from pomodouroboros.notifs import askForIntent, notify, setupNotifications
from pomodouroboros.pommodel import (
    Break,
    Day,
    IntentionResponse,
    Interval,
    Pomodoro,
)
from pomodouroboros.quickapp import Actionable, Status, mainpoint, quit
from pomodouroboros.storage import TEST_MODE, loadOrCreateDay, saveDay


fillRect = NSBezierPath.fillRect_


class BigProgressView(NSView):
    """
    View that draws a big red/green progress bar rectangle
    """

    _percentage = 0.0
    _leftColor = NSColor.greenColor()
    _rightColor = NSColor.redColor()

    def setPercentage_(self, newPercentage: float) -> None:
        """
        Set the percentage-full here.
        """
        self._percentage = newPercentage
        self.setNeedsDisplay_(True)

    def setLeftColor_(self, newLeftColor: NSColor) -> None:
        self._leftColor = newLeftColor

    def setRightColor_(self, newRightColor: NSColor) -> None:
        self._rightColor = newRightColor

    def drawRect_(self, rect: NSRect) -> None:
        bounds = self.bounds()
        split = self._percentage * (bounds.size.width)
        self._leftColor.set()
        fillRect(NSRect((0, 0), (split, bounds.size.height)))
        self._rightColor.set()
        fillRect(
            NSRect((split, 0), (bounds.size.width - split, bounds.size.height))
        )

    def canBecomeKeyView(self) -> bool:
        return False

    def movableByWindowBackground(self) -> bool:
        return True

    def acceptsFirstMouse_(self, evt: NSEvent) -> bool:
        return True

    def acceptsFirstResponder(self) -> bool:
        return False


class HUDWindow(NSWindow):
    """
    A window that doesn't receive input events and floats as an overlay.
    """

    def canBecomeKeyWindow(self) -> bool:
        return False

    def canBecomeMainWindow(self) -> bool:
        return False

    def acceptsFirstResponder(self) -> bool:
        return False

    def makeKeyWindow(self) -> None:
        return None


NSModalResponse = int


def getString(title: str, question: str, defaultValue: str) -> str:
    msg = NSAlert.alloc().init()
    msg.addButtonWithTitle_("OK")
    msg.addButtonWithTitle_("Cancel")
    msg.setMessageText_(title)
    msg.setInformativeText_(question)

    txt = NSTextField.alloc().initWithFrame_(NSRect((0, 0), (200, 100)))
    txt.setMaximumNumberOfLines_(5)
    txt.setStringValue_(defaultValue)
    msg.setAccessoryView_(txt)
    msg.window().setInitialFirstResponder_(txt)
    msg.layout()
    NSApp().activateIgnoringOtherApps_(True)

    response: NSModalResponse = msg.runModal()

    if response == NSAlertFirstButtonReturn:
        return txt.stringValue()
    else:
        return ""


@dataclass
class MacPomObserver(object):
    """
    Binding of model notifications interface to mac GUI
    """

    progressView: BigProgressView
    window: HUDWindow
    lastThreshold: float = field(default=0.0)
    thresholds: ClassVar[List[Tuple[float, str]]] = [
        (0.25, "Time to get started!"),
        (0.50, "Halfway there."),
        (0.75, "Time to finish up."),
        (0.95, "Almost done!"),
    ]
    active: bool = field(default=False)

    def __post_init__(self):
        print("post-init", self.active)
        self.window.setIsVisible_(self.active)

    def setWindow(self, newWindow: HUDWindow) -> None:
        """
        Change the window to be the new window.
        """
        self.window = newWindow
        print("set-window", self.active)
        newWindow.setIsVisible_(self.active)

    def breakStarting(self, startingBreak: Break) -> None:
        """
        A break is starting.
        """
        print("break start")
        self.active = True
        self.window.setIsVisible_(True)
        notify("Starting Break", "Take it easy for a while.")

    def pomodoroStarting(self, day: Day, startingPomodoro: Pomodoro) -> None:
        """
        A pomodoro is starting; time to express an intention.
        """
        print("pom start")
        self.active = True
        self.lastThreshold = 0.0
        self.window.setIsVisible_(True)
        askForIntent(lambda userText: expressIntention(day, userText))

    def elapsedWithNoIntention(self, pomodoro: Pomodoro) -> None:
        """
        A pomodoro completed, but no intention was specified.
        """
        notify(
            "Pomodoro Failed",
            informativeText=(
                "The pomodoro elapsed with no intention specified."
            ),
        )

    def progressUpdate(
        self,
        interval: Interval,
        percentageElapsed: float,
        canSetIntention: IntentionResponse,
    ) -> None:
        """
        Some time has elapsed on the given interval, and it's now
        percentageElapsed% done.  canSetIntention tells you the likely outcome
        of setting the intention.
        """
        baseAlphaValue = 0.15
        alphaVariance = 0.015
        pulseMultiplier = 1.5
        if canSetIntention == IntentionResponse.CanBeSet:
            self.progressView.setLeftColor_(NSColor.yellowColor())
            self.progressView.setRightColor_(NSColor.purpleColor())
            # boost the urgency on setting an intention
            baseAlphaValue += 0.1
            alphaVariance *= 2
            pulseMultiplier *= 2
        if canSetIntention == IntentionResponse.AlreadySet:
            # Nice soothing "You're doing it!" colors for remembering to set
            # intention
            self.progressView.setLeftColor_(NSColor.greenColor())
            self.progressView.setRightColor_(NSColor.blueColor())
            if (
                isinstance(interval, Pomodoro)
                and interval.intention is not None
            ):
                # TODO: maybe put reminder messages in the model?
                for pct, message in self.thresholds:
                    if self.lastThreshold <= pct and percentageElapsed > pct:
                        self.lastThreshold = percentageElapsed
                        notify(
                            "Remember Your Intention",
                            message,
                            "“" + interval.intention.description + "”",
                        )
        elif canSetIntention == IntentionResponse.OnBreak:
            # Neutral "take it easy" colors for breaks
            pulseMultiplier /= 2
            alphaVariance /= 2
            self.progressView.setLeftColor_(NSColor.lightGrayColor())
            self.progressView.setRightColor_(NSColor.darkGrayColor())
        elif canSetIntention == IntentionResponse.TooLate:
            # Angry "You forgot" colors for setting it too late
            self.progressView.setLeftColor_(NSColor.orangeColor())
            self.progressView.setRightColor_(NSColor.redColor())
        self.progressView.setPercentage_(percentageElapsed)
        alphaValue = (
            math.sin(rawSeconds() * pulseMultiplier) * alphaVariance
        ) + baseAlphaValue
        self.active = True
        self.window.setIsVisible_(True)
        self.window.setAlphaValue_(alphaValue)

    def dayOver(self):
        """
        The day is over, so there will be no more intervals.
        """
        self.active = False
        print("The day is over. Goodbye.")
        self.window.setIsVisible_(False)


def makeOneWindow(contentView) -> HUDWindow:
    app = NSApp()
    mainScreen = NSScreen.mainScreen()
    frame = mainScreen.frame()

    # build args for window initialization:
    #
    # - (instancetype)initWithContentRect:(NSRect)contentRect
    # - styleMask:(NSUInteger)windowStyle
    # - backing:(NSBackingStoreType)bufferingType defer:(BOOL)deferCreation

    contentRect = NSRect((200, 200), (frame.size.width - (200 * 2), 200))
    styleMask = NSBorderlessWindowMask
    backing = NSBackingStoreBuffered
    defer = False

    win = (
        HUDWindow.alloc()
        .initWithContentRect_styleMask_backing_defer_(
            contentRect,
            styleMask,
            backing,
            defer,
        )
        .retain()
    )
    win.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
    )
    win.setIgnoresMouseEvents_(True)
    win.setAlphaValue_(0.1)
    win.setContentView_(contentView)
    win.setBackgroundColor_(NSColor.blackColor())
    win.setLevel_(NSFloatingWindowLevel)
    win.orderFront_(app)
    return win


def expressIntention(day: Day, newIntention: str) -> None:
    """
    Express the given intention to the given day.
    """
    intentionResult = day.expressIntention(
        datetime.now(tz=tzlocal()), newIntention
    )
    print("IR", intentionResult)
    if intentionResult == IntentionResponse.WasSet:
        notify("Intention Set", f"“{newIntention}”")
    elif intentionResult == IntentionResponse.AlreadySet:
        description = day.pendingIntervals[
            0
        ].intention.description  # type: ignore
        notify(
            "Intention Not Set",
            "Already Specified",
            informativeText=f"intention was already: “{description}”",
        )
    elif intentionResult == IntentionResponse.TooLate:
        notify(
            "Intention Not Set",
            "Too Late",
            informativeText="It's too late to set an intention. "
            "Try again next time!",
        )
    elif intentionResult == IntentionResponse.OnBreak:
        notify(
            "Intention Not Set",
            "You're On Break",
            "Set the intention when the pom begins.",
        )
    else:
        notify(
            "Intention Confusion",
            "Internal Error",
            f"received {intentionResult}",
        )
        print("very surprised:", intentionResult)
    print("saving day")
    saveDay(day)
    print("saved")


def setIntention(day: Day) -> None:
    try:
        newIntention = getString(
            title="Set An Intention",
            question="What is your intention?",
            defaultValue="",
        )
        print("String Get")
        expressIntention(day, newIntention)
    except BaseException:
        print(Failure().getTraceback())


def thisAndPreviousPoms(day: Day) -> Iterable[Pomodoro]:
    if day.pendingIntervals:
        currentInterval = day.pendingIntervals[0]
        if isinstance(currentInterval, Pomodoro):
            yield currentInterval

    if day.elapsedIntervals:
        for each in reversed(day.elapsedIntervals):
            if isinstance(each, Pomodoro):
                yield each


def bonus(when: datetime, day: Day) -> None:
    """
    Start a new pom outside the usual bounds of pomodoro time, either before or
    after the end of the day.
    """
    day.bonusPomodoro(when)
    saveDay(day)


def now() -> datetime:
    return datetime.now(tz=tzlocal())


def newDay(forDate: date) -> Day:
    if TEST_MODE:
        print("Creating testing day")
        return Day.forTesting()
    else:
        print("New production-mode date", forDate)
        return loadOrCreateDay(forDate)


def labelForDay(day: Day) -> str:
    """
    Generate a textual label representing the success proportion of the given
    day.
    """
    success = len(day.successfulPomodoros())
    failed = len(day.failedPomodoros())
    mystery = len(day.unEvaluatedPomodoros())
    unfinished = len(day.pendingPomodoros())
    icon = tomato if success > failed else can
    title = icon + ": "
    title += f"{success}✓ "
    title += f"{failed}✗ "
    if mystery:
        title += f"{mystery}? "
    title += f"{unfinished}…"
    return title


can = "🥫"
tomato = "🍅"


@dataclass
class DayManager(object):
    observer: MacPomObserver
    window: HUDWindow
    progress: BigProgressView
    day: Day = field(default_factory=lambda: newDay(date.today()))
    loopingCall: Optional[LoopingCall] = field(default=None)

    @classmethod
    def new(cls) -> DayManager:
        progressView = BigProgressView.alloc().init()
        window = makeOneWindow(progressView)
        observer = MacPomObserver(progressView, window)
        self = DayManager(
            observer,
            window,
            progressView,
        )
        return self

    def recreateWindow(self) -> None:
        print("screens changed")
        newWindow = makeOneWindow(self.progress)
        self.observer.setWindow(newWindow)
        oldWindow = self.window
        self.window = newWindow
        oldWindow.close()

    def start(self) -> None:
        status = Status(can)
        status.menu(
            [
                ("Intention", lambda: setIntention(self.day)),
                ("Bonus Pomodoro", lambda: bonus(now(), self.day)),
                ("Previous was successful", lambda: self.setSuccess(True, -1)),
                ("This is successful", lambda: self.setSuccess(True, 0)),
                ("Failed previous", lambda: self.setSuccess(False, -1)),
                ("Oops, failed this one", lambda: self.setSuccess(False, 0)),
                ("Quit", quit),
            ]
        )

        def update() -> None:
            try:
                present = now()
                if present.date() != self.day.startTime.date():
                    self.day = newDay(date.today())
                self.day.advanceToTime(present, self.observer)
                status.item.setTitle_(labelForDay(self.day))
            except BaseException:
                print(Failure().getTraceback())

        self.loopingCall = LoopingCall(update)
        self.loopingCall.start(1.0 / 10.0)

    def setSuccess(self, succeeded: bool, index: int) -> None:
        for idx, aPom in enumerate(thisAndPreviousPoms(self.day)):
            if idx == -index:
                # this error testing should really be in the model
                if aPom.intention is None:
                    notify(
                        "Intention Not Set",
                        "Automatic Failure",
                        "Set an intention next time!",
                    )
                elif aPom.intention.wasSuccessful is not None:
                    adjective = (
                        "successful"
                        if aPom.intention.wasSuccessful
                        else "failed"
                    )
                    notify(
                        "Success Previously Set",
                        informativeText=f"Pomodoro Already {adjective}.",
                    )
                else:
                    self.day.evaluateIntention(aPom, succeeded)
                    adjective = (
                        "successful"
                        if aPom.intention.wasSuccessful
                        else "failed"
                    )
                    noun = (
                        "success"
                        if aPom.intention.wasSuccessful
                        else "failure"
                    )
                    notify(
                        f"pomodoro {noun}".title(),
                        informativeText=f"Marked Pomodoro {adjective}.",
                    )
                return


def callOnNotification(nsNotificationName: str, f: Callable[[], None]):
    NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        Actionable.alloc().initWithFunction_(f).retain(),
        "doIt:",
        nsNotificationName,
        None,
    )


@mainpoint()
def main(reactor: IReactorTCP) -> None:
    setupNotifications()
    dayManager = DayManager.new()
    dayManager.start()
    callOnNotification(
        NSApplicationDidChangeScreenParametersNotification,
        dayManager.recreateWindow,
    )
