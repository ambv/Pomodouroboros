from __future__ import annotations

from typing import Callable, Dict
from Foundation import NSError, NSObject

from UserNotifications import (
    UNAuthorizationOptionNone,
    # UNNotificationAction,
    UNNotificationCategory,
    UNNotificationRequest,
    # UNNotificationTrigger,
    UNTextInputNotificationAction,
    UNTimeIntervalNotificationTrigger,
    UNUserNotificationCenter,
    # UNNotificationContent,
    UNMutableNotificationContent,
    # UNNotificationPresentationOptionNone,
    UNNotificationPresentationOptionBanner,
    UNNotification,
    UNNotificationResponse,
    UNTextInputNotificationResponse,
)


class NotificationDelegate(NSObject):
    def init(self) -> NotificationDelegate:
        self.handlers: Dict[str, Callable] = {}
        return self

    def userNotificationCenter_willPresentNotification_withCompletionHandler_(
        self,
        notificationCenter: UNUserNotificationCenter,
        notification: UNNotification,
        completionHandler: Callable,
    ) -> None:
        print("asking about presenting notification")
        completionHandler(UNNotificationPresentationOptionBanner)

    def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
        self,
        notificationCenter: UNUserNotificationCenter,
        notificationResponse: UNNotificationResponse,
        completionHandler: Callable,
    ) -> None:
        # technically only UNTextInputNotificationResponse has userText
        handler = self.handlers.pop(
            notificationResponse.notification().request().identifier(), None
        )
        if handler is None:
            print("no handler")
        else:
            if isinstance(notificationResponse, UNTextInputNotificationResponse):
                userText = notificationResponse.userText()
                print("received response", userText)
                handler(userText)
            else:
                print("fail?")
        completionHandler()


notificationCenter = UNUserNotificationCenter.currentNotificationCenter()
categoryIdentifier = "SET_INTENTION_PROMPT"
basicCategoryIdentifier = "BASIC_MESSAGE"

theDelegate = NotificationDelegate.alloc().init()


def askForIntent(callback: Callable[[str], None]):
    identifier = "ask-for-intent"
    theDelegate.handlers[identifier] = callback
    content = UNMutableNotificationContent.alloc().init()
    content.setTitle_("Time To Set Intention")
    content.setBody_("What do you want to do right now?")
    content.setCategoryIdentifier_(categoryIdentifier)
    trigger = (
        UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(
            1, False
        )
    )
    request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
        identifier, content, trigger
    )

    def notificationRequestCompleted(error: NSError) -> None:
        print("notification requested", error)

    notificationCenter.addNotificationRequest_withCompletionHandler_(
        request, notificationRequestCompleted
    )


messageIdentifier = "basic-message"

def notify(title="", subtitle="", informativeText=""):
    content = UNMutableNotificationContent.alloc().init()
    content.setTitle_(title)
    content.setSubtitle_(subtitle)
    content.setBody_(informativeText)


    trigger = (
        UNTimeIntervalNotificationTrigger.triggerWithTimeInterval_repeats_(
            1, False
        )
    )

    request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
        messageIdentifier, content, trigger
    )

    def notificationRequestCompleted(error: NSError) -> None:
        print("notification requested", error)

    notificationCenter.addNotificationRequest_withCompletionHandler_(
        request, notificationRequestCompleted
    )


def setupNotifications():
    notificationCenter.setDelegate_(theDelegate)
    identifier = "SET_INTENTION"
    title = "Set Intention"
    options = 0
    # UNNotificationAction.actionWithIdentifier_title_options_(identifier, title, options)
    textInputButtonTitle = "Set Intent"
    textInputPlaceholder = "What would you like to do?"
    setIntentionAction = UNTextInputNotificationAction.actionWithIdentifier_title_options_textInputButtonTitle_textInputPlaceholder_(
        identifier, title, options, textInputButtonTitle, textInputPlaceholder
    )
    options = 0
    actions = [setIntentionAction]
    # I think these are mostly to do with Siri
    intentIdentifiers = []
    setIntentionPromptCategory = UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
        categoryIdentifier, actions, intentIdentifiers, options
    )
    basicMessageCategory = UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_(
        basicCategoryIdentifier, [], [], 0
    )

    def completionHandler(granted: bool, error: NSError) -> None:
        print("unusernotificationcenter auth completed", granted, error)

    notificationCenter.requestAuthorizationWithOptions_completionHandler_(
        UNAuthorizationOptionNone, completionHandler
    )
    notificationCenter.setNotificationCategories_(
        [basicMessageCategory, setIntentionPromptCategory]
    )
